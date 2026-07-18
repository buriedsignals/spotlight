# Spotlight dependency authority

The public Spotlight bootstrap installs no product dependencies. It verifies a
signed Buried Signals Engine archive with Minisign, then Engine resolves the
signed catalog, writes a sealed plan, and owns every dependency, model, skill,
runtime projection, update, rollback, and uninstall receipt.

Do not add `npm`, `pip`, Homebrew, model, OpenKnowledge, Obsidian, or QMD
installation commands to `install-spotlight.sh`. If a dependency changes, make
the change in the Engine product/catalog contract and test the sealed plan.

The only website prerequisites are `python3`, `curl`, and `minisign` when
Engine is not already available through Indicator Labs, `BSIG_BIN`, or PATH.
The configurator receives credentials on loopback and passes them to Engine on
stdin; it never writes a second dependency or runtime configuration.
