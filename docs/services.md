# Native Services

Continuum remains a host-local daemon. Install its login startup definition
per project:

```bash
continuum service install --project /path/to/project
continuum service status --project /path/to/project
continuum service remove --project /path/to/project
```

## Windows

Writes:

```text
%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Continuum Daemon.cmd
```

The startup launcher invokes `continuum up` at sign-in.

## macOS

Writes:

```text
~/Library/LaunchAgents/dev.continuum.<project-id>.plist
```

The plist runs `continuum daemon` in the foreground so `launchd` supervises
the actual daemon. After install, run the printed `launchctl bootstrap`
command. Logs are written below `.continuum/daemon_logs/`.

## Linux

Writes:

```text
~/.config/systemd/user/continuum-<project-id>.service
```

The user unit runs `continuum daemon` in the foreground. After install, run
the printed `systemctl --user daemon-reload && systemctl --user enable --now`
command. Inspect logs with `journalctl --user -u continuum-<project-id>`.
