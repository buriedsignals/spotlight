# Spotlight Plugin

This plugin installs Spotlight's agent-facing skills, agents, schemas, and
operator documentation. Selected methods are adapted from
[CTI Expert](https://github.com/7onez/cti-expert) by Hieu Ngo /
chongluadao.vn (GitHub: 7onez), reviewed at `f9ecc9b`, under the MIT License with
the upstream Ethical Use Addendum. The full record is preserved in `NOTICE.md`
and `third_party/`.
CTI updates arrive only through reviewed Spotlight releases; raw upstream
instructions are never fetched or activated by the plugin at runtime.

Report-diagram visual guidance adapts principles from
[diagram-design](https://github.com/cathrynlavery/diagram-design) by Cathryn
Lavery, reviewed at `09df49d8d1a1c7fb2efdfcdc7a2a0713534350a6` under the MIT
License. It is a design reference only: the plugin does not install, execute,
or redistribute the upstream project. See `NOTICE.md` for the full record.

Plugin install does not install runtime packages. It must not run `pip install`,
`npm install`, `uv tool install`, `npx`, or equivalent dependency commands.

For a full local runtime with reviewed dependency pins, run the canonical
installer (`install-spotlight.sh` at the repository root, advertised on the
hosted `setup.html` landing page):
`curl -fsSL https://spotlight.buriedsignals.com/install-spotlight.sh | bash`.
The installer uses exact versions recorded in `VALIDATED_DEPENDENCIES.md`.

RLM is optional and off by default. If a user enabled RLM during setup,
Spotlight proposes it during methodology approval for each case. RLM output is
lead-only and never verified or publishable evidence.
