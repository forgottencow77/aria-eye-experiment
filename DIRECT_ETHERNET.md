# Direct Ethernet operation

Configured on 2026-11-08 for the existing Mac USB Ethernet adapter and Aria Pi.
The experiment UI now runs on the Pi, using its local servo/LCD services.

## Addresses and pages

| Device | Interface | IPv4 address | Subnet mask |
| --- | --- | --- | --- |
| MacBook | USB 10/100/1000 LAN (`en7`) | `192.168.77.1` | `255.255.255.0` |
| Raspberry Pi | `eth0` | `192.168.77.2` | `255.255.255.0` |

No gateway or DNS server is configured for this direct link. Wi-Fi is not required
for the experiment, and the direct link does not replace the Mac's internet route.

- Administration: <http://192.168.77.2:8767/experiment/admin>
- Manual controls and 42-condition photography: <http://192.168.77.2:8767/study>
- Existing controls: <http://192.168.77.2:8767/>
- Participant links are created by this Pi-hosted admin page. Old Mac localhost
  links refer to the Mac server; use the Pi page's links for this deployment.

Power the Pi, connect Ethernet to the configured Mac adapter, and open one of the
URLs above. No terminal or SSH session needs to remain open. A different Ethernet
adapter on the Mac needs its own manual address configuration.

## Persistent configuration

The Pi NetworkManager profile `aria-direct-ethernet` has autoconnect enabled with
priority 100, IPv4 `192.168.77.2/24`, no default route, and IPv6 link-local enabled.
Existing connection profiles are retained.

`project-aria-servo-legacy-ui.service` is enabled at boot, with its code in
`/home/forgottencow/eye-display/legacy-ui`. It calls the servo service on
`127.0.0.1:8768` and the LCD service on `127.0.0.1:8769`.

The current configuration and experiment records are stored on the Pi at
`/var/lib/project-aria-experiment/sessions.json`. The initial deployment copied
all three existing Mac sessions and the current B₀=40 configuration. It did not
change their frozen settings or recorded responses. Mac data are retained in
`experiment_data/sessions.json` as the previous copy; future Pi answers are not
automatically synchronized back to the Mac. Export JSON/CSV from the Pi admin page.

## Verification

- The Mac route to `192.168.77.2` uses the wired `en7` interface.
- Admin, participant and study pages return HTTP 200 over that address.
- Both hardware APIs respond; the UI reports them connected with no active lease.
- Saved B₀ is 40%, with 42 slots / 39 distinct states and all three prior sessions.
- Network autoconnect and boot enablement of the UI, servo and LCD services are
  confirmed. A power-cycle test was not performed.
- Setup issued no servo motion or LCD color commands. The Pi system clock was
  synchronized to the Mac before enabling the experiment UI.

## Backups and adapter reuse

Pi backups are in `/home/forgottencow/eye-display/network-backup-20261108`.
Mac backups are in `experiment_data/direct-ethernet-backup-20261108`.

To use this Mac adapter on an ordinary DHCP network, restore DHCP with:

```sh
networksetup -setdhcp 'USB 10/100/1000 LAN'
```

To use the direct Aria link again:

```sh
networksetup -setmanual 'USB 10/100/1000 LAN' 192.168.77.1 255.255.255.0
```

Pi maintenance SSH uses the existing `~/.ssh/project_aria_codex` key and user
`forgottencow`. The existing host key can be verified using
`-o HostKeyAlias=raspberrypi.local`; private key contents are not part of the repo.
