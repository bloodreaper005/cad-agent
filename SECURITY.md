# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's **Report a
vulnerability** button on the Security tab of this repository, which opens a
private advisory visible only to the maintainers.

Please do not open a public issue for a suspected vulnerability, and please do
not include a customer's CAD model, workspace or knowledge database in a report.
A description of the behaviour and a minimal synthetic reproduction are more
useful and carry none of the risk.

Expect an acknowledgement within seven days and an assessment within thirty.

## What this project treats as a vulnerability

The agent produces evidence that a mechanical model is sound, and downstream
readers act on that evidence. So the failure that matters most here is not
remote code execution. It is **a model being certified that should not have
been**.

Reports in any of these areas are in scope:

- Causing a design to record as `completed`, or a validation report to be
  accepted, without the checks it claims having actually run.
- Registering a standard part into the catalogue under provenance it does not
  have, or under a trust tier it did not earn.
- Getting the FreeCAD runner to execute a script other than the reviewed,
  digest-pinned one, or to execute against an unpinned interpreter.
- Escaping the design session directory through any path the agent supplies:
  model paths, evidence paths, design identifiers, sizing or specification
  files.
- Anything in `fcstd_security.py`: archive traversal, decompression bombs,
  scripted objects surviving inspection, or executable payloads in an FCStd.
- Denial of service through a crafted input file, including one that makes a
  packaged FreeCAD script run without terminating.

## Known limitations, not vulnerabilities

These are properties of the design, documented rather than hidden. A report
that restates one of them will be closed with a pointer here.

- **The agent executes arbitrary Python in FreeCAD.** Modelling is done by an
  agent writing code and running it through a FreeCAD MCP bridge. That is the
  product working as intended, not a sandbox escape. Run the bridge loopback
  only; the runtime now checks this when
  `MECH_DESIGN_FREECAD_GUI_MCP_SETTINGS` points at the bridge's settings file,
  and reports the check as unverified when it does not.
- **The specification-level validation report is agent-authored.** The host
  independently re-verifies that the model opens, recomputes, contains no
  invalid objects and matches the recorded digest, through a nonce the agent
  never sees. The richer spec checks — dimensions, interference, fastener
  installation — are produced by a validator the agent runs, and the gate
  constrains that report's contract rather than reproducing its work. Closing
  that remaining gap is tracked work, not a defect report.
- **Calculation results are preliminary sizing evidence, not certification.**
  The `mechanics` and `gear_sizing` packages carry their assumptions and
  limitations in every result. A number you disagree with is an engineering
  discussion; open an issue.
- **Two values are curve fits to published charts** and say so at the point of
  use: the fatigue-strength fraction of Shigley figure 6-18 and the Neuber
  constant of equation 6-35. Both accept explicit overrides.

## Supported versions

The most recent release receives security fixes. This project has not yet
reached 1.0, so fixes land in a new minor release rather than being backported.

## Verifying what you run

The FreeCAD executable is pinned by SHA-256 and filesystem identity, re-checked
after every invocation. Every packaged FreeCAD script is pinned by SHA-256 in
`package_resources.PACKAGED_SCRIPT_DIGESTS` and verified before execution; a
script outside that manifest must have its digest supplied explicitly. If
either check fails, nothing runs.
