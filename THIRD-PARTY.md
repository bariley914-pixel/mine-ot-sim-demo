# Licences

## This repository

Licensed under the **GNU General Public License v3.0** (see [LICENSE](LICENSE)), to match
the upstream simulator it is built around. Copyright (C) 2026 Brock Riley for the files in
this repository (`devices/`, `monitor/`, `otsim/`, `scripts/`, docs).

## Upstream ot-sim (patsec/ot-sim) - GPL-3.0

* Source: <https://github.com/patsec/ot-sim>, pinned commit
  `9dc33047ff9fb474e61c8931c7dac7bf98df38ef` (2026-09-25).
* Licence: GNU GPL **version 3** (`COPYING` in upstream; README: "distributed under the GNU
  General Public License v3"). Upstream does not say "or any later version", so treat it as
  **GPL-3.0-only**. Copyright (C) 2021-2022 Patria Security, LLC.
* **No ot-sim source code is copied into this repository.** `otsim/Dockerfile` clones the
  pinned commit at build time, appends one `add_subdirectory()` line to its C++ CMakeLists so
  our `mine-dnp3-poll` tool builds in the same tree, and compiles the CPU, logic, Modbus,
  message-bus and DNP3 modules.
* **If you distribute the built `mine-ot-sim/otsim` image** (e.g. push it to a registry or
  hand someone a `docker save` tarball) you are distributing GPL-3.0 binaries: you must give
  recipients the licence text and the Corresponding Source (the pinned upstream commit plus
  `otsim/Dockerfile` and `otsim/dnp3-poll/`). Sharing this repo and letting people build it
  themselves satisfies that naturally.

## Built into the device image

| Component | Licence |
|---|---|
| opendnp3 (ot-sim submodule) | Apache-2.0 |
| fmt, nlohmann/json, cppzmq (ot-sim submodules) | MIT |
| libzmq, CZMQ (Debian packages) | MPL-2.0 |
| Go modules used by ot-sim (expr, mbserver/modbus, zmq4, etree, ...) | MIT / BSD-style (see upstream `go.sum`) |
| Debian bookworm base image | various free licences |

## Monitor (otmon-mine)

| Component | Licence |
|---|---|
| modbus-tk 1.1.5 | LGPL-2.1-or-later |
| PyYAML 6.0.3 | MIT |
| Python 3.11 slim base image | PSF + Debian licences |

The dashboard uses **no third-party front-end code** (no CDN, no JS/CSS libraries).

Not legal advice. Check before selling or embedding in a commercial product.
