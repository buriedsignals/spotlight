# Passive Indicator Triage — Compact

Use for one domain, IP address, URL, or file hash. Treat every external classification as a dated lead.

1. Preserve the original value; record the type and a separately normalized lookup form.
2. Check passive/public records only. Do not browse to a suspicious URL, scan an IP, submit a private file, or install a tool.
3. For every observation record: provider, exact label/value, source URL, collection UTC, provider observation time, access method, and saved artifact.
4. Seek one independent source and one plausible contradiction. Do not use majority vote or average incompatible provider scores.
5. Mark each planned path `observed`, `null`, `skipped`, or `blocked`; explain skipped/blocked paths.
6. Keep provider labels in research notes. Promote only the narrower claim the evidence grounds.

Minimum output:

```text
input_original | input_type | lookup_form
observation | provider | observed_at | collected_at | source | artifact
inference | alternative_explanation | contradiction | missing_path
```

WARNING: A direct request can alert or harm infrastructure and can expose the investigator. Use passive records or an archived copy first.
