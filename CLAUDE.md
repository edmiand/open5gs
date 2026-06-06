# Claude Code Prompt — Deploy Open5GS (edmiand fork) on a New arm64 Ubuntu VM

---

## Context

Deploy a 5G Core Network using the Open5GS source fork at
`https://github.com/edmiand/open5gs` on a fresh **arm64 Ubuntu 22.04 (Jammy)**
VM. Follow the official build-from-source procedure at
`https://open5gs.org/open5gs/docs/guide/02-building-open5gs-from-sources/`,
but substitute the upstream repo with the fork. Install into `~/open5gs/install`
so the directory structure matches our existing lab VM.

Target 5G Core configuration:
- **PLMN:** MCC `999`, MNC `70`
- **TAC:** `1`
- **NSSAI SST:** `1`
- **AMF NGAP address:** the VM's primary non-loopback IP (auto-detect it)
- **UPF GTP-U address:** same as the AMF NGAP address (single-NIC setup)
- **TUN interface:** `ogstun`, subnet `10.45.0.1/16` + `2001:db8:cafe::1/48`

---

## What to Do

### 1. Prerequisites check

Verify the OS is Ubuntu 22.04 arm64. Print `lsb_release -a` and `uname -m`.
Fail early with a clear message if the OS or architecture does not match.

### 2. MongoDB 8.0

Install MongoDB 8.0 following the official procedure for Ubuntu 22.04 (Jammy),
including the `arm64` repo line:

```
deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ]
    https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/8.0 multiverse
```

Start and enable `mongod`. Verify it is running with `systemctl is-active mongod`.

### 3. Build dependencies

Install all packages listed in the official guide:

```
python3-pip python3-setuptools python3-wheel ninja-build build-essential
flex bison git cmake libsctp-dev libgnutls28-dev libgcrypt-dev libssl-dev
libmongoc-dev libbson-dev libyaml-dev libnghttp2-dev libmicrohttpd-dev
libcurl4-gnutls-dev libtins-dev libtalloc-dev meson
```

Then handle the `libidn-dev` / `libidn11-dev` conditional exactly as the
official guide specifies (check `apt-cache show libidn-dev` first).

### 4. Clone the fork

```bash
cd ~
git clone https://github.com/edmiand/open5gs
cd open5gs
```

Install the directory as `~/open5gs` (the clone already creates this).

### 5. Build and install

```bash
meson build --prefix=$(pwd)/install
ninja -C build
```

Run only the 5G Core registration test to verify the build:

```bash
./build/tests/registration/registration
```

Then install:

```bash
cd build && ninja install && cd ..
```

### 6. Configure for lab PLMN and correct IP addresses

Auto-detect the VM's primary non-loopback IP:

```bash
VM_IP=$(ip route get 1.1.1.1 | awk '{print $7; exit}')
```

Edit the installed YAML configs in `~/open5gs/install/etc/open5gs/` using
`sed` or Python — **do not use a text editor interactively**. Apply these
changes programmatically:

#### `nrf.yaml`
- Set `mcc: 999`, `mnc: 70` under `nrf.serving[0].plmn_id`

#### `amf.yaml`
- Set `mcc: 999`, `mnc: 70` in **all three** `plmn_id` blocks
  (guami, tai, plmn_support)
- Set `tac: 1`
- Set AMF NGAP server `address: $VM_IP`
- Confirm `s_nssai` has `sst: 1`

#### `upf.yaml`
- Set UPF GTP-U server `address: $VM_IP`

Leave all other NF configs at their defaults (127.0.x.x addresses are fine
for intra-host SBI communication).

After editing, print a diff of each changed file so changes are visible.

### 7. TUN device (persistent via systemd-networkd or rc.local)

Create a startup script at `/usr/local/bin/open5gs-netconf.sh`:

```bash
#!/bin/bash
ip tuntap add name ogstun mode tun 2>/dev/null || true
ip addr add 10.45.0.1/16 dev ogstun 2>/dev/null || true
ip addr add 2001:db8:cafe::1/48 dev ogstun 2>/dev/null || true
ip link set ogstun up
sysctl -w net.ipv4.ip_forward=1
sysctl -w net.ipv6.conf.all.forwarding=1
iptables -t nat -C POSTROUTING -s 10.45.0.0/16 ! -o ogstun -j MASQUERADE 2>/dev/null \
  || iptables -t nat -A POSTROUTING -s 10.45.0.0/16 ! -o ogstun -j MASQUERADE
ip6tables -t nat -C POSTROUTING -s 2001:db8:cafe::/48 ! -o ogstun -j MASQUERADE 2>/dev/null \
  || ip6tables -t nat -A POSTROUTING -s 2001:db8:cafe::/48 ! -o ogstun -j MASQUERADE
```

Make it executable. Create a systemd unit
`/etc/systemd/system/open5gs-netconf.service` that runs this script at boot
(`After=network.target`, `RemainAfterExit=yes`). Enable and start the unit.
Verify `ogstun` is up with `ip addr show ogstun`.

Disable UFW if it is active (`sudo ufw disable`).

### 8. WebUI

Install Node.js 20 via the Nodesource method (the official guide procedure —
import GPG key, add nodistro repo, `apt install nodejs`).

Then:

```bash
cd ~/open5gs/webui
npm ci
```

Create a systemd unit `/etc/systemd/system/open5gs-webui.service` that runs
`npm run dev` in `~/open5gs/webui` with `PORT=9999` and `HOSTNAME=0.0.0.0`,
as the current user. Enable but **do not start** it yet (leave start to the
operator so it doesn't conflict with demo sessions).

### 9. Convenience: NF start/stop helper script

Create `~/open5gs/start-5gc.sh` that starts the 5G Core NFs in the correct
order (matching the NRF → SCP → AMF → SMF → UPF → AUSF → UDM → PCF → NSSF
→ BSF → UDR sequence), each as a background process writing to its standard
log path `~/open5gs/install/var/log/open5gs/<nf>.log`. Include a `stop` mode
that `pkill`s all `open5gs-*d` processes.

Also create `~/open5gs/status-5gc.sh` that checks whether each NF process is
running and prints a one-line summary (running / stopped) for each.

### 10. Smoke test

After setup, run the following checks and print PASS/FAIL for each:

1. `mongod` is active
2. `ogstun` interface exists and has `10.45.0.1/16` assigned
3. `~/open5gs/install/bin/open5gs-amfd --help` exits 0
4. AMF YAML contains the detected `$VM_IP` as the NGAP address
5. NRF YAML contains `mcc: 999` and `mnc: 70`

---

## Constraints and Notes

- All file edits must be **non-interactive** (no `vi`, `nano`, etc.).
- Do not modify source files in `~/open5gs/` after the build — only touch
  `install/etc/open5gs/*.yaml`.
- Use `sudo` only where required (package installs, sysctl, iptables,
  systemd unit operations, tun device setup).
- If any step fails, print the error clearly and stop — do not silently
  continue past failures.
- The `~/open5gs/install/` prefix must be used for all paths (not
  `/usr/local` or system-wide install), consistent with the existing lab VMs.
- This is a **5G Core only** deployment — skip all 4G EPC NFs (MME, SGW-C,
  SGW-U, HSS, PCRF).
