# Contributing

Your contributions are always welcome!

## Guidelines

* Add one link per Pull Request.
    * Make sure the PR title is in the format of `Add project-name`.
    * Write down the reason why the project is awesome.
* Add the link: `* [project-name](http://example.com/) - A short description ends with a period.`
    * Keep descriptions concise and **short**.
* Add a section if needed.
    * Add the section description.
    * Add the section title to Table of Contents.
* Search previous Pull Requests or Issues before making a new one, as yours may be a duplicate.
* Only submit projects whose **PCB is actually designed in KiCad** and whose KiCad source files (`.kicad_pcb`, `.pro`, `.kicad_pro`, etc.) are publicly available.
* Don't mention `KiCad` in the description as it's implied.
* Check your spelling and grammar.
* Remove any trailing whitespace.

Just a gentle reminder: **Try not to submit your own project**. Instead, wait for someone finds it useful and submits it for you :)

## Repository Evidence and Quality Metadata

`data/projects.json` stores curated project details plus automatically refreshed repository facts. `data/evidence.json` stores human-reviewed physical-build, hardware-test and certification evidence; automation must not overwrite it with keyword guesses.

The 100-point repository-evidence score is weighted as follows:

| Area | Points | Example signals |
| --- | ---: | --- |
| Design sources | 20 | PCB, schematic, project file and project-local libraries |
| Reproducibility | 25 | BOM, Gerbers, placement files, assembly/build instructions and PDF schematic |
| Verification | 25 | Reviewed real-build photo, reviewed hardware-test evidence and automated ERC/DRC |
| Maintenance | 15 | Recent activity, not archived, CI and issue tracking |
| Documentation and openness | 15 | Open license, README, description and additional documentation/wiki |

The score describes **public evidence and reproducibility**, not electrical correctness or engineering quality. Unknown evidence earns no points and must remain `unknown`, not be inferred from a render or an ambiguous filename. A verified `real_build_photo` or `hardware_tested` signal requires a direct evidence URL and review date.

Run the deterministic audit without third-party dependencies:

```sh
python scripts/audit_projects.py --url https://github.com/owner/project
python scripts/audit_projects.py --all
python scripts/audit_projects.py --evidence-only
python scripts/audit_projects.py --added-on YYYY-MM-DD --readme-only
python scripts/audit_projects.py --validate-only
```

Set `GITHUB_TOKEN` for higher API limits. The audit also tracks forks, open issues, archive state, default branch, source-file counts, KiCad generator/file-format versions and evidence timestamps. Popularity metrics are stored for context but do not affect the score.

## Dead Link Policy

We check all links are valid with [awesome_bot](https://github.com/dkhamsing/awesome_bot).

* If status codes of `503`, `404` are found, the link will be marked as `(DNS)` or `(Error)` in italics.
* If the link is still unreachable after **30 days**, then it will be deleted.

## Updating your PR

A lot of times, making a PR adhere to the standards above can be difficult.
If the maintainers notice anything that we'd like changed, we'll ask you to
edit your PR before we merge it. There's no need to open a new PR, just edit
the existing one. If you're not sure how to do that,
[here is a guide](https://github.com/RichardLitt/knowledge/blob/master/github/amending-a-commit-guide.md)
on the different ways you can update your PR so that we can merge it.
