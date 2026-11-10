"""Chunks as LlamaIndex nodes. The only module here that depends on LlamaIndex."""
import uuid

from llama_index.core.schema import TextNode


def to_nodes(chunks) -> list:
    """One TextNode per chunk. The heading path is embedded with the text (it is what makes a chunk like
    "Dosing" retrievable); source, pages and kind travel as metadata for citation but are not embedded.
    Ids carry a per-call batch id, so a re-upload can insert the new nodes before deleting the old ones."""
    batch = uuid.uuid4().hex[:8]
    nodes = []
    for chunk in chunks:
        metadata = {"source": chunk.source, "kind": chunk.kind}
        if chunk.section:
            metadata["section"] = chunk.section
        if chunk.pages:
            metadata["pages"] = chunk.pages
        nodes.append(TextNode(
            id_=f"{chunk.source}::{batch}::{chunk.index}",
            text=chunk.text,
            metadata=metadata,
            excluded_embed_metadata_keys=["source", "kind", "pages"],
        ))
    return nodes
