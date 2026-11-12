# Material intake

You can bring your own material into a case: an interview, a source's email, field notes, a document you were handed, a dataset, a photo or a video. Spotlight keeps the original, records where it came from, takes exact passages from it, and lets you decide which claims drawn from those passages go into your knowledge. This is an optional step. It is not a phase and it is not a gate, and it involves no investigation and no fact-check.

The rules the code follows are in `AGENTS.md` ("Material intake" and "Protected cases"). This page is the user's view.

## Add material

Put a file in the case, or let Spotlight copy it:

```sh
scripts/spotlight-python scripts/intake.py CASE_DIR add --file ~/Desktop/interview-notes.txt
scripts/spotlight-python scripts/intake.py CASE_DIR scan
```

`add` copies one file into `CASE_DIR/intake/` and drafts its record. `scan` drafts a record for every file you already dropped under `intake/`. Each kind has its own folder (`interviews/`, `emails/`, `notes/`, `documents/`, `datasets/`, `media/`, `other/`). A file whose kind its name does not give, such as a PDF or an audio file, waits in `intake/unsorted/` until you name its kind. A recording or a scan holds no text to quote, so a text version (a transcript) is added next to it with `intake.py derive`. The original is never changed.

## What the Intake page asks

Open the Intake page from the review server's "Material intake" button, or, in a chat app with the Spotlight review MCP, ask the agent to open it. Each item of material is a section you can open and close; the first one you have not approved is open, and approving one closes it and opens the next. In each item the page asks you to:

1. Check what is recorded about the item and its rights. One folded line sums it up, for example "Dock worker 1 (pseudonym) · yes, not attributed by name · direct quotes not allowed · consent spoken, on the recording". Opening it shows six fields: the kind of material, how the source is shown (a pseudonym or a real name), whether it may be published (yes on the record, yes not attributed by name, background only, or no off the record), direct quotes, how consent was given, and who holds the rights. These apply to every claim from that item. When no rights are recorded yet, the line says so and the most restrictive choices are selected: a new record starts as restrictive as possible (`case_only`), so anything you leave unsaid stays unpublishable. You may approve content marked off the record; that is your choice.
2. Tick the claims you keep. Each claim shows its sentence (click it to rewrite it; the new sentence is saved when you click away), its structure as small tags, and the passage it came from. One button, "Show what was said around each", shows the words around every passage of the item. A claim a model suggested leaving out arrives unticked and dimmed, with the reason, and stays out unless you tick it.
3. Press "Approve N claims". One approval saves the item's rights, keeps the ticked claims and drops the others, confirms the record (unless it is already confirmed) and approves the claims. If a step is refused, the page shows the reason beside the button, the item stays open and nothing after that step happens. A confirmed record does not change, so its rights are shown but cannot be edited. Approving with no claim ticked is refused.

In the newsroom edition the page asks for your name once at the top, and approving needs it.

Confirming and approving happen on this page. The page asks Spotlight for a one-time code for that item and uses it for that one decision, and the page never shows it to you or to the agent. A command typed on the command line without the code is refused, and a confirmation or approval that someone wrote into the case's files by hand does not count: Spotlight ignores it and the page shows the item as not yet decided. What you confirm is the record as saved by your approval, and what you approve is the record, the kept claims and their passages with their rights. Changing the item's passages, claims or rights afterwards removes the approval.

A passage is accepted only when its text appears in the original word for word, at one place. An approved claim enters your knowledge as what the source said. It carries no fact-check verdict.

## When there is no investigation

A case that holds approved material and no brief goes from your approval straight to the Ingest decision. A case with an investigation shows the approved claims in the same Ingest batch as its findings. The Ingest page shows under each claim from material the passage it rests on, which material it came from (its kind and file name), and the passage's attribution, consent and sensitivity in words, so you see the rights you are about to ingest.

## What leaves your computer

Spotlight sends nothing. The original stays in the case folder. In the solo edition, approved claims and their passages are written to your wiki when you approve Ingest (`docs/knowledge-destination.md`). In the newsroom edition the material is held in the case: the newsroom's service does not accept it yet.

If you ask a model to propose passages, that model reads the material. When that must not reach a cloud model, use a protected case.

## Protected cases

A cases folder named `cases_protected`, or one that contains a file named `.spotlight-protected`, is protected. The Pi harness runs every case in it with no web, no cloud model and no research swarm, and writes it to that folder's own wiki (`cases_protected/wiki`), never to your main wiki. Material marked `case_only` can be written to a wiki only from a protected case.

This is enforced in the Pi harness. A runtime that can run shell commands can still read the folder, and Spotlight does not encrypt it. For a stronger boundary, see [sensitivity.md](sensitivity.md).
