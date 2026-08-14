# Spotlight dependency authority

Spotlight has two production installers with separate ownership receipts:

- Indicator Labs uses Engine's signed catalog and sealed plan. Engine owns its
  dependency, model, skill, projection, update, rollback, and uninstall state.
- `install-spotlight.sh` is the Engine-free public installer described in
  `README.md`. It installs only the exact reviewed versions declared in that
  script and records its own file manifest for safe update and uninstall.

Keep both authorities aligned. A dependency change must update the Engine
catalog/product plan, the public installer's exact pin, and the corresponding
contract tests. Neither path may request an unpinned `latest` dependency.

The public configurator receives credentials only on loopback and writes them
to the local owner-readable environment file; it does not send them to Buried
Signals infrastructure or place them in downloadable artifacts.
