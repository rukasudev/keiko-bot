# Releasing Keiko

Merging into `main` never deploys. Production changes only when a version is
published.

## Publish a version

1. Merge the pull request into `main`. The CI workflow runs the test suite on
   every pull request and on every push to `main`.
2. Publish the version from `main`:

   ```bash
   gh release create v1.0.1 --target main --generate-notes
   ```

   The Release workflow runs the suite on that commit, builds the image as
   `keiko-bot:v1.0.1` (and `keiko-bot:latest`), deploys exactly that version,
   and checks the container is still running 30 seconds later.

## Choose the number

Versions follow `vMAJOR.MINOR.PATCH`:

- a fix that changes nothing else bumps PATCH (`v1.0.0` → `v1.0.1`);
- a new feature, or a visible change to an existing one, bumps MINOR
  (`v1.0.1` → `v1.1.0`);
- a change that breaks what admins or stored documents rely on bumps MAJOR.

## Run a previous version again

Actions → Release → Run workflow, with the version to run (`v1.0.0`). Nothing
is rebuilt: the deploy pulls the image that version published.

## Credentials

The image carries no credential. The deploy hands the AWS keys to the
container when it starts, and the bot reads every other secret from SSM with
them (`AppConfig.get_ssm_configs`).
