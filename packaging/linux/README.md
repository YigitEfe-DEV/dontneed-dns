# The Linux installer scripts

This directory holds the shell installer of the source archive:

| File | What it does |
| --- | --- |
| `install.sh` | creates the virtual environment in `${XDG_DATA_HOME:-$HOME/.local/share}/dontneed-dns/venv`, installs the project from the bundled wheelhouse (no network access needed) and registers the systemd user services |
| `uninstall.sh` | stops and removes the services, removes the virtual environment and keeps the configuration unless `--purge` is given |
| `fedora_entry.py` | the entry point of the self contained executable of the Fedora edition; it is not used by the shell installer |

**This directory is not a Python package.**  It has no `pyproject.toml` and
no `setup.py`, so `pip install .` or `pip install -e .` fails here with

```
Directory '.../packaging/linux' is not installable. Neither 'setup.py' nor 'pyproject.toml' found.
```

To install the project from the source tree, go to the **project root**,
the directory that holds `pyproject.toml`, and run the commands of
[docs/linux.md](../../docs/linux.md).

On Fedora the shortest way is the single executable of the release; it needs
neither Python nor pip.
