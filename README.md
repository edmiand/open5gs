<p align="center"><a href="https://open5gs.org" target="_blank" rel="noopener noreferrer"><img width="100" src="https://open5gs.org/assets/img/open5gs-logo-only.png" alt="Open5GS logo"></a></p>

## Getting Started

Please follow the [documentation](https://open5gs.org/open5gs/docs/) at [open5gs.org](https://open5gs.org/)!

## Sponsors

If you find Open5GS useful for work, please consider supporting this Open Source project by [Becoming a sponsor](https://github.com/sponsors/acetcom). To manage the funding transactions transparently, you can donate through [OpenCollective](https://opencollective.com/open5gs).

<p align="center">
  <a target="_blank" href="https://open5gs.org/#sponsors">
      <img alt="sponsors" src="https://open5gs.org/assets/img/sponsors.svg">
  </a>
</p>

## Community

- Problem with Open5GS can be filed as [issues](https://github.com/open5gs/open5gs/issues) in this repository.
- Other topics related to this project are happening on the [discussions](https://github.com/open5gs/open5gs/discussions).
- Voice and text chat are available in Open5GS's [Discord](https://discordapp.com/) workspace. Use [this link](https://discord.gg/GreNkuc) to get started.

## Contributing

If you're contributing through a pull request to Open5GS project on GitHub, please read the [Contributor License Agreement](https://open5gs.org/open5gs/cla/) in advance.

## License

- Open5GS Open Source files are made available under the terms of the GNU Affero General Public License ([GNU AGPL v3.0](https://www.gnu.org/licenses/agpl-3.0.html)).
- [Commercial licenses](https://open5gs.org/open5gs/support/) are also available from [NewPlane](https://newplane.io/) at [sales@newplane.io](mailto:sales@newplane.io).

## Support

Technical support and customized services for Open5GS are provided by [NewPlane](https://newplane.io/) at [support@newplane.io](mailto:support@newplane.io).

---

## Lab Deployment — edmiand fork, arm64 Ubuntu 22.04

Deploy a 5G Core Network from this fork on a fresh **arm64 Ubuntu 22.04 (Jammy)** VM.
Install into `~/open5gs/install` (matches existing lab VMs).

**Target configuration**

| Parameter | Value |
|-----------|-------|
| PLMN | MCC `999`, MNC `70` |
| TAC | `1` |
| NSSAI SST | `1` |
| AMF NGAP address | VM's primary non-loopback IP (auto-detected) |
| UPF GTP-U address | same as AMF NGAP (single-NIC) |
| TUN interface | `ogstun` — `10.45.0.1/16`, `2001:db8:cafe::1/48` |

### Step 1 — Prerequisites

```bash
lsb_release -a && uname -m   # must show Ubuntu 22.04 + aarch64
```

Fail early if OS or arch doesn't match.

### Step 2 — MongoDB 8.0

```bash
curl -fsSL https://www.mongodb.org/static/pgp/server-8.0.asc \
  | sudo gpg -o /usr/share/keyrings/mongodb-server-8.0.gpg --dearmor
echo "deb [ arch=amd64,arm64 signed-by=/usr/share/keyrings/mongodb-server-8.0.gpg ] \
  https://repo.mongodb.org/apt/ubuntu jammy/mongodb-org/8.0 multiverse" \
  | sudo tee /etc/apt/sources.list.d/mongodb-org-8.0.list
sudo apt update && sudo apt install -y mongodb-org
sudo systemctl enable --now mongod
systemctl is-active mongod
```

### Step 3 — Build dependencies

```bash
sudo apt install -y python3-pip python3-setuptools python3-wheel ninja-build \
  build-essential flex bison git cmake libsctp-dev libgnutls28-dev libgcrypt-dev \
  libssl-dev libmongoc-dev libbson-dev libyaml-dev libnghttp2-dev \
  libmicrohttpd-dev libcurl4-gnutls-dev libtins-dev libtalloc-dev meson
# libidn conditional (as per official guide)
apt-cache show libidn-dev &>/dev/null \
  && sudo apt install -y libidn-dev \
  || sudo apt install -y libidn11-dev
```

### Step 4 — Clone and build

```bash
cd ~
git clone https://github.com/edmiand/open5gs
cd open5gs
meson build --prefix=$(pwd)/install
ninja -C build
./build/tests/registration/registration   # 5GC smoke test
cd build && ninja install && cd ..
```

### Step 5 — Configure PLMN and IP addresses

```bash
VM_IP=$(ip route get 1.1.1.1 | awk '{print $7; exit}')
# nrf.yaml  — set mcc/mnc under nrf.serving[0].plmn_id
# amf.yaml  — set mcc/mnc in all three plmn_id blocks, tac: 1, NGAP address: $VM_IP
# upf.yaml  — set GTP-U address: $VM_IP
# All edits via sed/Python (non-interactive). Print diff after each file.
```

### Step 6 — TUN device (persistent)

Create `/usr/local/bin/open5gs-netconf.sh`:

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

Create and enable `/etc/systemd/system/open5gs-netconf.service`
(`After=network.target`, `RemainAfterExit=yes`). Disable UFW.

### Step 7 — WebUI

```bash
# Node.js 20 via Nodesource, then:
cd ~/open5gs/webui && npm ci
```

Create `/etc/systemd/system/open5gs-webui.service` running `npm run dev`
with `PORT=9999 HOSTNAME=0.0.0.0`. Enable but **do not start** yet.

### Step 8 — Helper scripts

- `~/open5gs/start-5gc.sh` — starts NFs in order: NRF → SCP → AMF → SMF → UPF → AUSF → UDM → PCF → NSSF → BSF → UDR; each logs to `install/var/log/open5gs/<nf>.log`. Also accepts `stop` to `pkill open5gs-*d`.
- `~/open5gs/status-5gc.sh` — prints running/stopped for each NF.

### Step 9 — Smoke test

```
mongod active                         PASS/FAIL
ogstun has 10.45.0.1/16              PASS/FAIL
open5gs-amfd --help exits 0          PASS/FAIL
AMF YAML contains $VM_IP             PASS/FAIL
NRF YAML contains mcc:999 mnc:70     PASS/FAIL
```

### Constraints

- All file edits non-interactive (no `vi`/`nano`).
- Only modify `install/etc/open5gs/*.yaml` — not source files.
- Use `sudo` only where required.
- Fail loudly on any error — do not silently continue.
- 5G Core only — skip all 4G EPC NFs (MME, SGW-C/U, HSS, PCRF).
