# Why the planning directory stays private

Phase 9 decision D-02 excludes the workshop planning directory from publication.
It contains founder-private career and permission context, owner acceptance
records, operational setup and local-machine references. Publishing the process
scaffolding would also bury the evidence a reviewer needs in several megabytes
of planning history.

The workshop and product are separate repositories with independent histories.
The product began from an explicit approved file set; private workshop material
has never entered its history. Removing a file later would not remove its earlier
Git objects, which is why this boundary applies before every publication.

The curated replacements are the README judgment story, retained and visibly
framed [provenance evidence](../provenance/), and the cited authority summaries
in the [public decision register](register.md). Historical bodies and intentional
private provenance references remain documented without publishing their targets.
The citation inventory annotates each unresolved occurrence with a public
successor or this boundary explanation. Runtime storage references, historical
research artifacts and excluded process notes are retained citations rather than
claims that those files ship in the product.

Current unresolved citations are checked separately from authenticated historical
removals and repoints. The scanner records actual occurrences; it does not infer
missing documents or reconstruct an unobserved spike. Ignore rules prevent
accidental additions, while the privacy scanner proves the complete candidate
tree and reachable history contain only approved material.
