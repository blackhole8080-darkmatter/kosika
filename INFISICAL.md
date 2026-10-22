Secrets manager in use: none

# Secrets and environment variables

Kosika reads everything it needs from the process environment
(`os.environ`), so no application code has to change: the variables below
are found exactly as before whether they come from a `.env` file or from
`infisical run`.

## Local development commands

- `kosika start` — the easiest way in: creates a folder of starter agents if
  you have none, finds a model already on this computer, and opens Kosika
  Studio.
- `kosika studio FOLDER` — opens Kosika Studio for the agents under `FOLDER`.
- `python -m kosika.cli ...` — the same commands when the `kosika` command is
  not on `PATH`.

`make dev` runs the local dev command through Infisical (see `Makefile`):

```text
infisical run --env=dev -- kosika start
```

Build, test, lint and the production/distribution path are left alone; they
do not go through Infisical. Tests run with `python -m pytest`.

## Environment variables Kosika reads

- `KOSIKA_MODEL_URL`
- `KOSIKA_MODEL`
- `KOSIKA_MODEL_API_KEY`
- `KOSIKA_API_KEY`
- `KOSIKA_DOCUMENTS_DIR`
- `KOSIKA_MUTATION_SINK`
- `OLLAMA_MAX_LOADED_MODELS`

Names only — never commit values. `.env.example` holds the same names with
placeholder values, and `.env` and its variants are ignored by git.

## Finish setup

Create a project for this service at <https://app.infisical.com>. Then, from
this folder, one command per line:

```text
# Install the Infisical CLI (pick the line for your platform)
brew install infisical/get-cli/infisical
npm install -g @infisical/cli
scoop bucket add org https://github.com/Infisical/scoop-infisical.git
scoop install infisical

# Sign in (use `infisical login -i` on WSL 2, Codespaces or SSH with no browser)
infisical login

# Link this folder to the project you created (writes .infisical.json)
infisical init

# Import your .env: drag it onto the project's Secrets Overview page and
# choose the dev environment. Every key is imported at once. Do this before
# the file leaves this folder.

# Start the app with secrets injected from Infisical
infisical run --env=dev -- kosika start

# Then move .env OUT of this repository folder (do not rename it to a backup
# inside the folder — git would not ignore that) and run the command above
# again. If the app still works, its values now come from Infisical.
```

`.infisical.json` holds the project id, contains no secrets, and is safe to
commit.

### CI and production

Production entry points (the built distribution, Docker, CI) do not use the
dev CLI. Give them a machine identity with Universal Auth and set
`INFISICAL_UNIVERSAL_AUTH_CLIENT_ID` and
`INFISICAL_UNIVERSAL_AUTH_CLIENT_SECRET` in the platform's secret store.

### Rotate anything ever committed

A secret that was ever committed stays in git history, so treat it as public
and rotate it in Infisical and at the provider. `infisical scan` finds
leaked secrets.
