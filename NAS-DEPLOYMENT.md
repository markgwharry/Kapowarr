# NAS deployment

This fork is deployed from `/volume3/projects/kapowarr` with Docker Compose.
Application source stays in Git; the live SQLite database is kept separately at
`/volume3/docker/personal/appdata/kapowarr` and must be included in NAS backups.

The Compose project name remains `kapowarr-dev` so the migration preserves the
existing deployment identity. Watchtower updates are disabled because this image
is built from local source changes.

Deploy with:

```sh
docker compose up -d --build
```
