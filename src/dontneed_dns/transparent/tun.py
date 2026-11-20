"""The TUN device and the routes of the transparent router.

Some traffic cannot be redirected with nftables, because the redirect
destroys the address the packet was sent to and the receiver needs it.
The UDP traffic of the target addresses is such traffic.

The router therefore sends the packets of the targets into a TUN device
and reads them there.  The packets keep their original addresses, so the
relay knows where they were sent and to which port.
"""

from __future__ import annotations

import os
import socket
import struct
import subprocess
from pathlib import Path

__all__ = ["RouteManager", "TunDevice", "TunError"]


class TunError(Exception):
    """The TUN device or its routes could not be prepared."""


#: The ioctl that creates a TUN device.
TUNSETIFF = 0x400454CA

#: The interface flags of a TUN device.
IFF_TUN = 0x0001

#: The flag that turns off the packet information header.
IFF_NO_PI = 0x1000

#: The default address of the device.  It is only used so that the kernel
#: accepts the routes; no traffic is ever sent to it.
DEFAULT_TUN_ADDRESS = "10.99.0.1/30"


class TunDevice:
    """A TUN device that carries the packets of the targets."""

    def __init__(self, name: str = "dnd0", address: str = DEFAULT_TUN_ADDRESS) -> None:
        self.name = name
        self.address = address
        self._fd: int | None = None

    def open(self) -> None:
        """Create the device and bring it up."""
        if self._fd is not None:
            return
        import fcntl

        try:
            fd = os.open("/dev/net/tun", os.O_RDWR)
        except OSError as exc:
            raise TunError(f"the TUN device could not be opened: {exc}") from exc
        ifr = struct.pack("16sH14s", self.name.encode(), IFF_TUN | IFF_NO_PI, b"")
        try:
            fcntl.ioctl(fd, TUNSETIFF, ifr)
        except OSError as exc:
            os.close(fd)
            raise TunError(f"the TUN device could not be created: {exc}") from exc
        self._fd = fd
        self._configure()

    def _configure(self) -> None:
        """Give the device an address and bring it up."""
        run(["ip", "addr", "replace", self.address, "dev", self.name])
        run(["ip", "link", "set", self.name, "up"])

    def read(self, size: int = 65535) -> bytes:
        """Read one packet from the device."""
        if self._fd is None:
            raise TunError("the TUN device is not open")
        return os.read(self._fd, size)

    def write(self, packet: bytes) -> None:
        """Inject one packet into the network stack."""
        if self._fd is None:
            raise TunError("the TUN device is not open")
        os.write(self._fd, packet)

    def close(self) -> None:
        """Close the device and remove it."""
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
        run(["ip", "link", "delete", self.name], check=False)


class RouteManager:
    """The routes and the rules that send the target traffic into the TUN.

    The traffic of the user is looked up in a table of its own.  Only the
    target addresses are in that table; everything else falls through to
    the main table, so the rest of the machine keeps its normal path.
    The traffic of the router itself carries a mark and uses the main
    table, which prevents the routing loop.
    """

    def __init__(
        self,
        tun_name: str = "dnd0",
        *,
        table: int = 100,
        uid: int | None = None,
        mark: int = 2,
        source: str | None = None,
    ) -> None:
        self.tun_name = tun_name
        self.table = table
        self.uid = uid
        self.mark = mark
        self.source = source

    # -- helpers ----------------------------------------------------------

    @staticmethod
    def host_address(family: int = socket.AF_INET) -> str:
        """The address of the host that is used for the outgoing traffic.

        The address of the interface that carries the default route is
        used.  When there is no default route, the address of the first
        interface that is not the loopback device is taken.
        """
        flag = "-6" if family == socket.AF_INET6 else "-4"
        interface = ""
        result = subprocess.run(
            ["ip", "route", "show", "default"], capture_output=True, text=True, timeout=10
        )
        words = result.stdout.split()
        if "dev" in words:
            interface = words[words.index("dev") + 1]
        if not interface:
            result = subprocess.run(["ip", "-o", "link", "show"], capture_output=True, text=True, timeout=10)
            for line in result.stdout.splitlines():
                name = line.split(":")[1].strip().split("@")[0] if ":" in line else ""
                if name and name != "lo":
                    interface = name
                    break
        if interface:
            result = subprocess.run(
                ["ip", flag, "-o", "addr", "show", "dev", interface],
                capture_output=True,
                text=True,
                timeout=10,
            )
            words = result.stdout.split()
            for key in ("inet", "inet6"):
                if key in words:
                    candidate = words[words.index(key) + 1]
                    if "/" in candidate:
                        return candidate.split("/")[0]
        return "127.0.0.1" if family == socket.AF_INET else "::1"

    def _source(self, family: int = socket.AF_INET) -> str:
        if self.source:
            return self.source
        return self.host_address(family)

    # -- rules ------------------------------------------------------------

    def install_rules(self) -> None:
        """Install the rule that sends the traffic of the user to the table."""
        self.remove_rules()
        if self.uid is not None:
            run(
                [
                    "ip",
                    "rule",
                    "add",
                    "priority",
                    "100",
                    "uidrange",
                    f"{self.uid}-{self.uid}",
                    "lookup",
                    str(self.table),
                ]
            )
            run(
                [
                    "ip",
                    "-6",
                    "rule",
                    "add",
                    "priority",
                    "100",
                    "uidrange",
                    f"{self.uid}-{self.uid}",
                    "lookup",
                    str(self.table),
                ]
            )
        # The traffic of the router itself uses the main table.
        run(["ip", "rule", "add", "priority", "90", "fwmark", f"0x{self.mark:x}", "lookup", "main"])
        run(["ip", "-6", "rule", "add", "priority", "90", "fwmark", f"0x{self.mark:x}", "lookup", "main"])

    def remove_rules(self) -> None:
        """Remove the rules again."""
        run(["ip", "rule", "del", "priority", "100"], check=False)
        run(["ip", "rule", "del", "priority", "90"], check=False)
        run(["ip", "-6", "rule", "del", "priority", "100"], check=False)
        run(["ip", "-6", "rule", "del", "priority", "90"], check=False)

    # -- routes -----------------------------------------------------------

    def set_targets(self, addresses: list[str]) -> None:
        """Send the traffic of the given addresses into the TUN device."""
        self.clear_targets()
        source4 = self._source(socket.AF_INET)
        source6 = self._source(socket.AF_INET6)
        for address in addresses:
            if ":" in address:
                run(
                    [
                        "ip",
                        "-6",
                        "route",
                        "replace",
                        address,
                        "dev",
                        self.tun_name,
                        "src",
                        source6,
                        "table",
                        str(self.table),
                    ]
                )
            else:
                run(
                    [
                        "ip",
                        "route",
                        "replace",
                        address,
                        "dev",
                        self.tun_name,
                        "src",
                        source4,
                        "table",
                        str(self.table),
                    ]
                )

    def clear_targets(self) -> None:
        """Remove every route of the table."""
        run(["ip", "route", "flush", "table", str(self.table)], check=False)
        run(["ip", "-6", "route", "flush", "table", str(self.table)], check=False)

    def remove(self) -> None:
        """Remove the routes and the rules."""
        self.clear_targets()
        self.remove_rules()


def run(command: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run a command of the system and fail loudly."""
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    if check and result.returncode != 0:
        raise TunError(f"the command failed: {' '.join(command)}\n{result.stderr.strip()}")
    return result


def tun_path() -> Path:
    """The path of the TUN device."""
    return Path("/dev/net/tun")
