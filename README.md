# tak-feeder-vr

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
| Callsign | `VR IC 59`; commuter trains by line, `VR R`; other operators by their code, e.g. `FENNIARAIL T 3021` |
| Type | `a-n-G-E-V-C-M` (generic neutral vehicle; ATAK has no civilian train symbol) |
| Speed / course | m/s as CoT requires (ATAK shows the user's units); course computed from consecutive positions |
| `ce` | GPS accuracy from Digitraffic |
| Stale | 120 s |

The remarks hold the route, next stop with estimate and delay, speed,
rolling stock, operator, GPS time, the data source and hashtags:

```
VR IC 59 · Kaukoliikenne
Helsinki → Oulu
Seuraava: Pasila 12:00 (+3 min)
Nopeus: 108 km/h
Kalusto: Sr2, 7 vaunua, 189 m, max 200 km/h
Operaattori: VR-Yhtymä Oyj
GPS: 12:00:00 (±8 m)
Juna 59, lähtöpäivä 2026-10-13
Lähde: Fintraffic / digitraffic.fi, CC 4.0 BY
#vr #IC59 #kaukoliikenne
```

Category hashtags: `#kaukoliikenne`, `#lähiliikenne`, `#tavaraliikenne`,
`#veturi`, `#vaihtotyö`, `#ratatyökone`, `#koeajo`.

### Commuter trains also published by HSL

HSL publishes the commuter trains of the Helsinki region, and
[tak-feeder-hsl](https://github.com/sgofferj/tak-feeder-hsl) /
[trakbridge-plugin-hsl](https://github.com/sgofferj/trakbridge-plugin-hsl)
send them to TAK. To avoid two markers for one train, this feeder also listens
to HSL's train feed (`/hfp/v2/journey/ongoing/vp/train/#` on `mqtt.hsl.fi`)
and pairs trains by line letter, operating day, distance and heading, one to
one. `HSL_MODE` decides what happens to a paired train:

- `uid` (default): sent with the UID tak-feeder-hsl uses,
  `HSL-train-<route>-<oday>-<start>-<dir>`, so ATAK shows one marker. Its
  callsign and remarks alternate between the two feeders.
- `skip`: not sent; HSL's marker is the only one.
- `off`: HSL is ignored.

## Running

The image is built for amd64 and arm64: `ghcr.io/street3r/tak-feeder-vr`.

```bash
git clone https://github.com/Street3r/tak-feeder-vr.git
cd tak-feeder-vr
cp .env.example .env          # COT_URL, certificate paths, HSL_MODE
mkdir certs                   # cert.pem and key.pem (unencrypted) go here
docker compose up -d
docker compose logs -f
```

The feeder logs a summary every five minutes: CoT sent, trains paired with
HSL, trains without timetable, and MQTT message counts.

### Certificate

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
| `HSL_MODE` | `uid` | `uid`, `skip` or `off` |
| `CATEGORIES` | all | e.g. `Long-distance,Commuter` |
| `OPERATOR_CALLSIGNS` | | e.g. `fenniarail=FR` |
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
[CC 4.0 BY](https://creativecommons.org/licenses/by/4.0/). The feeder
converts the data to CoT and adds derived values (course, next stop). HSL
data (used only for pairing trains) is © HSL, CC BY 4.0. The data is provided
as is, without warranty.
