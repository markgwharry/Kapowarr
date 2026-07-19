# NAS deployment

This fork is deployed from `/volume3/projects/kapowarr` with Docker Compose.
Application source stays in Git; the live SQLite database is kept separately at
`/volume3/docker/personal/appdata/kapowarr` and must be included in NAS backups.

The portable `docker-compose.yml` remains suitable as an upstream-style example.
NAS-specific paths and the local image build live in `docker-compose.nas.yml`.
Its Compose project name remains `kapowarr-dev` so deployment identity is
preserved. Watchtower updates are disabled because this image is built from local
source changes.

Deploy with:

```sh
docker compose -f docker-compose.nas.yml up -d --build
```
