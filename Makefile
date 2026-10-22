# Kosika local development.
#
# `make dev` starts the app with the Infisical project's "dev" secrets already
# in the environment, so nothing has to be read from a .env file. Install the
# Infisical CLI and run `infisical login` and `infisical init` first (see
# INFISICAL.md). Dependencies are installed with pip; this target does not
# install anything.

.PHONY: dev

dev:
	infisical run --env=dev -- kosika start
