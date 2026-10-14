# tak-feeder-vr

> [!WARNING]
> **Work in progress. Not recommended for use.** The feeder has not been run
> against live Digitraffic data or a live TAK server yet. Things may change or
> break without notice.

Feeds the live GPS positions of trains running in Finland into a TAK server
as CoT. The data comes from Fintraffic's open
[Digitraffic rail API](https://www.digitraffic.fi/rautatieliikenne/) and
covers VR and the other operators on the Finnish network.

## How it works

- One MQTT connection to `wss://rata.digitraffic.fi/mqtt` brings all trains in
  Finland: GPS positions (`train-locations/#`, about every 6 s per train),
  timetables (`trains/#`) and compositions (`compositions/#`).
- At start the feeder loads the timetables of trains running now
  (`/live-trains`) plus the station and operator lists (refreshed daily).
  Data a train still lacks is fetched one train at a time, at most 20 requests
  a minute.
- Every `UPDATE_INTERVAL` seconds each train whose position changed is sent
  to TAK; unchanged trains are re-sent every `REFRESH_INTERVAL` seconds. A
  train is dropped when its GPS has been silent for `MAX_POSITION_AGE` seconds.
- Only trains with a GPS position are sent. Not every train has GPS, and the
  driver switches it on and off.

### CoT

| Field | Value |
|---|---|
| UID | `VR-<departure date>-<train number>`, or the HSL UID (see below) |
| Callsign | `VR IC 59`; commuter lines of HSL and Nysse as those feeders name them, `HSL I`, `NYSSE M`; other commuter lines by operator, `VR R`; other operators by an abbreviation (`FENNIARAIL`, `GRK`, `NRC`, `NORTHRAIL`) or their Digitraffic code (`DESTIA T 3021`) |
| Type | `a-f-G-E-V-C-M`, the same as tak-feeder-hsl (generic vehicle; ATAK has no civilian train symbol) |
| Speed / course | m/s as CoT requires (ATAK shows the user's units); course computed from consecutive positions |
| `ce` | GPS accuracy from Digitraffic; unknown (`9999999`) when it reports 0, as it currently does for every train |
| Stale | 120 s |

The remarks hold the route, next stop with estimate and delay, speed,
rolling stock, operator, GPS time, the data source and hashtags:

```
VR IC 59 · Long-distance
Helsinki → Oulu
Next: Pasila 12:00 (+3 min)
Speed: 108 km/h
Rolling stock: Sr2, 7 cars, 189 m, max 200 km/h
Operator: VR-Yhtymä Oyj
GPS: 12:00:00
Train 59, departure date 2026-10-13
Source: Fintraffic / digitraffic.fi, CC BY 4.0
#vr #IC59 #kaukoliikenne #longdistance #vryhtymä
```

The operator's full name without the company form is a tag too
(`#vryhtymä`, `#fenniarail`, `#grkrail`, `#nrcgroupfinland`, `#northrail`).
Category hashtags, Finnish and English: `#kaukoliikenne #longdistance`,
`#lähiliikenne #commuter`, `#tavaraliikenne #cargo`, `#veturi #locomotive`,
`#vaihtotyö #shunting`, `#ratatyökone #trackmachine`, `#koeajo #testrun`,
`#tyhjävaunujuna #emptystock`.

### Commuter lines and authorities

VR runs every commuter train, and Digitraffic reports `vr` as the operator
for all of them. The authority that orders a line is not in the data, so the
feeder maps line letters (`COMMUTER_BRANDS`):

| Lines | Callsign | Extra tag | Remarks |
|---|---|---|---|
| A, E, I, K, L, P, U, X, Y (HSL's own lines) | `HSL I` | `#hsl` | `Authority: HSL` |
| M (Tampere region) | `NYSSE M` | `#nysse` | `Authority: NYSSE` |
| R, Z, D, T, G, O, H and others | `VR R` | | |
| V (train type HV): empty stock between depot and station, no passengers | `VR HV 10149` | `#tyhjävaunujuna #emptystock` instead of the commuter tags | `Empty stock` |

R, Z, D and T also stop in the HSL area with HSL tickets, but they are not
HSL's own lines. The map is a setting, so it can follow changes.

### Commuter trains also published by HSL

HSL publishes the commuter trains of the Helsinki region, and
[tak-feeder-hsl](https://github.com/Street3r/tak-feeder-hsl) (fork of
[sgofferj/tak-feeder-hsl](https://github.com/sgofferj/tak-feeder-hsl)) and
[trakbridge-plugin-hsl](https://github.com/sgofferj/trakbridge-plugin-hsl)
send them to TAK. The simplest way to avoid two markers is to drop trains
from the HSL feeder; docker-tak-feeder-compose does that with
`HSL_EXCLUDE_MODES=train`. That is what the default `HSL_MODE=off` assumes.

If an HSL feeder does send trains, this feeder can listen to HSL's train feed
(`/hfp/v2/journey/ongoing/vp/train/#` on `mqtt.hsl.fi`) and pair trains by
line letter, operating day, distance and heading, one to one:

- `uid`: sent with the UID tak-feeder-hsl uses,
  `HSL-train-<route>-<oday>-<start>-<dir>`, so ATAK shows one marker. Both
  feeders use the same CoT type, so the symbol stays the same.
- `skip`: not sent; HSL's marker is the only one.
- `off` (default): HSL is ignored.

## Running

The image is built for amd64 and arm64: `ghcr.io/street3r/tak-feeder-vr`.

```bash
git clone https://github.com/Street3r/tak-feeder-vr.git
cd tak-feeder-vr
cp .env.example .env          # COT_URL, certificate paths
mkdir certs                   # cert.pem and key.pem (unencrypted) go here
docker compose up -d
docker compose logs -f
```

The feeder logs a summary every five minutes: CoT sent, trains without
timetable, trains paired with HSL (if enabled), and MQTT message counts.

### Certificate

The container runs as uid 10001, so the key must be readable by that user
(`chown 10001 key.pem`), or add `user: "0:0"` to the service. A Docker health
check reports the container unhealthy when Digitraffic data stops arriving
(`HEALTH_MAX_SILENCE`, default 600 s). When the TAK server is down the feeder
reconnects by itself and keeps its train data.

The feeder connects with a TAK client certificate and an unencrypted key in
PEM format. TAK looks up the LDAP user whose name equals the certificate CN
and applies its groups, so create a certificate-only user (e.g. `feeder-vr`)
that belongs only to the write group (e.g. `VR_WRITE`). Users who should see
the trains need the matching read group. An encrypted key can be converted
with `openssl rsa -in key.pem -out key-nopw.pem`.

### Configuration

All settings are environment variables, documented in
[`.env.example`](.env.example). The most important:

| Variable | Default | Purpose |
|---|---|---|
| `COT_URL` | `udp://239.2.3.1:6969` | TAK server, e.g. `ssl://takserver:8090` |
| `PYTAK_TLS_CLIENT_CERT`, `PYTAK_TLS_CLIENT_KEY` | | Client certificate and key (PEM). `CLIENT_CERT`/`CLIENT_KEY` also work |
| `PYTAK_TLS_DONT_VERIFY` | `0` | `1` = do not verify the TAK server certificate |
| `DIGITRAFFIC_USER` | `tak-feeder-vr` | `Digitraffic-User` header. No personal data |
| `HSL_MODE` | `off` | `uid`, `skip` or `off` |
| `COMMUTER_BRANDS` | HSL lines, `M=NYSSE` | Line letter to authority, e.g. `I=HSL` |
| `CATEGORIES` | all | e.g. `Long-distance,Commuter`; `Empty stock` is its own category, so this leaves out the depot trains |
| `OPERATOR_CALLSIGNS` | `ferfi=FENNIARAIL,winco=GRK,vr-track=NRC,operail=NORTHRAIL` | Callsign abbreviations by Digitraffic operator code; replaces the default list, empty = codes upper-cased |
| `DEBUG` | `0` | `1` logs every CoT event |

## Limitations

- Trains without GPS are not shown. GPS data can be missing or wrong;
  Fintraffic filters out positions more than 500 m from the track.
- Digitraffic data is not meant for safety-critical use.
- Course is computed from movement and is held while a train stands still.

## Development

```bash
pip install .
PYTHONPATH=tests python -m unittest discover -s tests
```

## Credits

This feeder is based on the TAK feeders by **Stefan Gofferje**
([@sgofferj](https://github.com/sgofferj)), used with his permission:

- [tak-feeder-traffic-fin-ais](https://github.com/sgofferj/tak-feeder-traffic-fin-ais):
  Digitraffic MQTT to TAK
- [tak-feeder-hsl](https://github.com/sgofferj/tak-feeder-hsl) and
  [trakbridge-plugin-hsl](https://github.com/sgofferj/trakbridge-plugin-hsl):
  pytak worker structure, CoT format, HSL UID scheme

Built with [pytak](https://github.com/snstac/pytak) and
[Eclipse Paho MQTT](https://github.com/eclipse-paho/paho.mqtt.python).

## License

Code: Copyright (C) 2026 Street3r, with parts derived from the work of
Stefan Gofferje. Licensed under the GNU General Public License v3.0 or later;
see [LICENSE](LICENSE).

Data: Source: Fintraffic / digitraffic.fi, license
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The feeder
converts the data to CoT and adds derived values (course, next stop, line
authority). HSL data (only used for pairing trains when `HSL_MODE` is not
`off`) is © HSL, CC BY 4.0. The data is provided
as is, without warranty.
