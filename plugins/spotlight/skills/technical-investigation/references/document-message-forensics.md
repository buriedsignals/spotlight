# Local Document and Raw-Message Forensics

## Evidence Safety

Operate on local material supplied or lawfully collected for the case. Preserve the original before analysis: record source, collection UTC, filename, byte size, and a cryptographic hash, then analyze a copy. Keep outputs in the case directory.

WARNING: Do not open active content, execute macros or scripts, follow links, load remote images, launch embedded objects, or run attachments. Use parsers that do not execute the document. Never upload sensitive documents or messages to a public analysis service without explicit approval.

## Document Workflow

1. Identify the container and actual file type independently of the extension.
2. Extract filesystem/container metadata and application metadata as separate layers.
3. Record exact fields such as creator/producer, creation/modification strings, revision count, language, template, embedded filenames, and document relationships only when present.
4. Preserve the original field string, parsed value, tool and version, command or method, stderr/warnings, and output artifact.
5. Compare metadata with visible dates, publication records, archive captures, and the chain of custody.
6. Treat discrepancies as questions. A creation date can reflect a template, conversion, copied filesystem attribute, clock error, or deliberate edit.

Metadata rarely proves authorship. Software names, usernames, paths, printers, template names, and time zones are pivots whose meaning requires independent corroboration.

## Raw Email Workflow

Use complete raw source when possible; a screenshot omits routing and authentication evidence.

1. Preserve the raw message and hash it before parsing.
2. Separate the visible `From`, envelope/return path, message ID, date, MIME structure, authentication results, and each `Received` header.
3. Unfold continuation lines without changing their original representation in the preserved artifact.
4. Read the `Received` chain from the earliest header added by a trusted boundary toward the recipient. Identify where trust begins; earlier self-supplied headers may be forged.
5. Normalize timestamps while retaining original strings and offsets. Flag impossible ordering, large clock skew, missing hops, private addresses, and malformed fields.
6. Interpret SPF, DKIM, and DMARC in their exact scope. A pass can show that a domain-authorized system handled a message; it does not establish the human sender or the truth of the content.
7. Resolve any network indicators through passive historical sources for the relevant time, then return to `epistemic-grounding`.

## Output

Create a table of observations with artifact reference, exact field, parsed value, original timestamp, normalized timestamp, source layer, limitations, and possible alternatives. Keep authorship, origin, and delivery-path conclusions separate. Preserve contradictions rather than silently selecting the cleanest metadata story.
