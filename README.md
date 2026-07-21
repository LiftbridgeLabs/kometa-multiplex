# kometa-multiplex

**Curate artwork once, apply it everywhere.** A template for running one
[Kometa](https://kometa.wiki) instance against multiple Plex servers, with
[AURA](https://github.com/mediux-team/aura) (the MediUX companion app) as the
single curation UI and a small exporter that propagates your saved art to
every library on every server — plus [ImageMaid](https://github.com/Kometa-Team/ImageMaid)
to clean up the image bloat that art churn leaves behind.

```
                ┌─────────────┐
   you, once ──▶│  AURA (UI)  │ pick MediUX sets for your main server
                └──────┬──────┘
                       │ AURA.db (saved sets)
                       ▼
             aura-to-kometa.py (nightly, 05:30)
                       │ metadata/mediux-auto-*.yml  (keyed by TMDb/TVDb id)
                       ▼
                ┌─────────────┐        ┌──────────────┐
                │   Kometa    │───────▶│ Plex server A │  (main - AURA watches this one)
                │ (one config,│───────▶│ Plex server B │
                │  06:00 run) │───────▶│ Plex server C │  ...as many as you like
                └─────────────┘        └──────────────┘
                       ▲
        overlays / collections / operations applied on top of the art
```

## Why this works

- Kometa metadata files match **movies by TMDb id** and **shows by TVDb id** —
  ids are universal, so one exported file applies cleanly to any server that
  has the title. Servers that lack a title just skip it.
- AURA and Kometa already have a handshake: AURA removes Kometa's `Overlay`
  label when it downloads new art (`RemoveOverlayLabelOnlyOnPosterDownload`),
  which tells the next Kometa run to re-apply overlays on top of the new art.
  Art stays fresh, overlays stay on, nobody fights.
- ImageMaid runs weekly per server to delete the orphaned poster files this
  churn generates inside Plex's data directory.

## What's in the box

| File | What it is |
|---|---|
| `config.example.yml` | Kometa config: N servers / libraries via YAML anchors, secrets as `<<placeholders>>` |
| `kometa-compose.yml` | Kometa + the nightly exporter sidecar + per-server ImageMaids |
| `aura-compose.yml` | The single AURA curation instance |
| `aura-configs/*/config.yaml.template` | AURA config with `${VAR}` placeholders |
| `aura-to-kometa.py` | The exporter: AURA.db → Kometa metadata files (TMDb→TVDb fallback for shows whose paths lack `{tvdb-NNN}`) |
| `render-aura-configs.py` | Fills the AURA templates from `.env` (AURA can't read env vars itself) |
| `check_paths.py` | Static preflight: verifies every path in config.yml resolves before you ever run Kometa |
| `.env.example` | Every secret the stack needs, in one gitignored file |
| `overlays/movies/cam.yml`, `overlays/tv/series_status.yml` | The custom overlay definitions `config.example.yml` wires up |
| `overlays/images/` | The art those overlays and the network-logo default need — bundled so it works out of the box, not assumed to already be on your machine |

## Quickstart

1. Copy `.env.example` → `.env`, fill it in. (`.env` is gitignored — keep it that way.)
2. Copy `config.example.yml` → `config.yml`; set your servers, libraries, and
   library mapping names. One `plex:` block per extra server via the anchor
   pattern shown inline.
3. Edit `aura-configs/main-server/config.yaml.template` (server URL, library
   names) and run `python render-aura-configs.py` to produce the deployable
   `config.yaml`.
4. Deploy this folder to your Kometa host as its `/config` mount; adjust the
   volume paths in both compose files; `docker compose up -d` both stacks.
   Keep the `.env` next to the compose files (compose reads it automatically).
5. Validate before the first real run:
   `docker exec kometa python kometa.py --config /config/config.yml --validate-config --validate-level full`
6. Roll out one library at a time:
   `docker exec -d kometa python kometa.py --config /config/config.yml --run --run-libraries "YourFirstLibrary"`
   First TV runs are long — that's the one-time cost of pushing every poster,
   season poster, and titlecard. Daily runs after that are minutes.

## Scheduling that doesn't collide

| When | What |
|---|---|
| 00:00 | AURA AutoDownload refreshes art for saved sets |
| 01:00–05:00 | (leave free — Plex's own maintenance window) |
| 05:30 | Exporter regenerates the metadata files |
| 06:00 | Kometa applies everything, everywhere |
| Sun 09:00+ | ImageMaids, one per server, staggered an hour apart |

## The trade-off to know about

AURA curates **only what its Plex server has**. Titles that exist solely on
your other servers can't be saved in AURA — for those, keep a small manual
metadata file (same `url_poster:` format, listed *before* the auto file so
AURA wins if the title ever lands on the main server). The exporter's output
in git doubles as your disaster-recovery record: if AURA.db dies, the last
committed export rebuilds every poster.

## Secrets

Nothing in this repo should ever contain a real token. Kometa reads secrets
from `KOMETA_*` environment variables (the `<<name>>` placeholders in
config.yml); AURA gets them baked in at render time from `.env`; compose
interpolates the rest. Commit templates, gitignore the rendered/real files —
the `.gitignore` here is already set up for that.
