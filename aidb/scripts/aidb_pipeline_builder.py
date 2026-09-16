#!/usr/bin/env python3
"""
aidb_pipeline_builder.py — Interactive AIDB pipeline SQL generator.

Generates the SQL needed to create an AIDB pipeline based on user inputs.
Validates step sequencing before generating. Prints ready-to-run SQL.

Usage:
    python3 scripts/aidb_pipeline_builder.py --help
    python3 scripts/aidb_pipeline_builder.py \\
        --name my_pipeline \\
        --source documents \\
        --source-key-column id \\
        --source-data-column content \\
        --steps ParsePdf ChunkText KnowledgeBase \\
        --model my_embed_model \\
        --mode Background

Exit codes:
    0 — SQL generated successfully
    1 — Validation error
"""

import sys
import argparse
import json

# Step sequencing rules: maps step name to (input_type, output_type)
# Types: 'text', 'bytes', 'image_bytes', 'vector'
STEP_IO = {
    "ChunkText":      ("text",        "text"),
    "SummarizeText":  ("text",        "text"),
    "ParseHtml":      ("bytes",       "text"),
    "ParsePdf":       ("bytes",       "text"),
    "PdfToImage":     ("bytes",       "image_bytes"),
    "PerformOcr":     ("image_bytes", "text"),
    "KnowledgeBase":  ("text",        "vector"),
    "KnowledgeBaseImage": ("image_bytes", "vector"),
}

VALID_STEPS = list(STEP_IO.keys())
VALID_MODES = ["Live", "Background", "Disabled"]


def validate_sequence(steps: list[str]) -> tuple[bool, str]:
    """Validate that steps form a compatible input/output chain."""
    if not steps:
        return False, "At least one step is required."
    
    current_type = None
    for i, step in enumerate(steps):
        if step not in STEP_IO:
            return False, f"Unknown step '{step}'. Valid steps: {', '.join(VALID_STEPS)}"
        
        in_type, out_type = STEP_IO[step]
        
        if current_type is None:
            # First step — source type is determined by the step's input
            current_type = out_type
        else:
            if current_type != in_type:
                # Special case: KnowledgeBase accepts both text and image_bytes
                if step == "KnowledgeBase" and current_type == "image_bytes":
                    pass  # OK: image KB step
                else:
                    return False, (
                        f"Step {i+1} ({step}) expects '{in_type}' input, "
                        f"but previous step produces '{current_type}'. "
                        f"Incompatible sequence."
                    )
            current_type = out_type
    
    last_step = steps[-1]
    if last_step not in ("KnowledgeBase", "KnowledgeBaseImage", "SummarizeText", "PerformOcr", "ParsePdf", "ParseHtml", "PdfToImage"):
        # Warn if pipeline doesn't end on a terminal step
        print(f"WARN: Pipeline ends on '{last_step}' which produces '{STEP_IO[last_step][1]}'. "
              f"Consider ending with KnowledgeBase for embedding pipelines.")
    
    return True, "OK"


def build_step_options(step: str, model: str | None, chunk_size: int, overlap: int, 
                        distance: str) -> str:
    """Generate step_N_options SQL expression."""
    if step == "ChunkText":
        return f"aidb.chunk_text_config(desired_length => {chunk_size}, overlap_length => {overlap})"
    elif step == "SummarizeText":
        if not model:
            return "'{}'"
        return f"aidb.summarize_text_config(model => '{model}')"
    elif step == "ParseHtml":
        return "aidb.html_parse_config()"
    elif step == "ParsePdf":
        return "aidb.pdf_parse_config(method => 'Structured')"
    elif step == "PdfToImage":
        return """'{"dpi": 300, "format": {"type": "png"}, "render_annotations": true}'::JSONB"""
    elif step == "PerformOcr":
        if not model:
            raise ValueError("PerformOcr requires --model (must be nim_paddle_ocr provider)")
        return f"aidb.ocr_config('{model}')"
    elif step in ("KnowledgeBase", "KnowledgeBaseImage"):
        if not model:
            raise ValueError("KnowledgeBase step requires --model")
        data_format = "Image" if step == "KnowledgeBaseImage" else "Text"
        dist = distance or "Cosine"
        return (
            f"aidb.knowledge_base_config(\n"
            f"        model => '{model}',\n"
            f"        data_format => '{data_format}',\n"
            f"        distance_operator => '{dist}',\n"
            f"        vector_index => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)\n"
            f"    )"
        )
    return "'{}'"


def generate_sql(args) -> str:
    steps = args.steps
    sql_parts = [
        f"SELECT aidb.create_pipeline(",
        f"    name => '{args.name}',",
        f"    source => '{args.source}',",
    ]
    if args.source_key_column:
        sql_parts.append(f"    source_key_column => '{args.source_key_column}',")
    if args.source_data_column:
        sql_parts.append(f"    source_data_column => '{args.source_data_column}',")
    if args.destination:
        sql_parts.append(f"    destination => '{args.destination}',")
    
    sql_parts.append(f"    auto_processing => '{args.mode}',")
    
    for i, step in enumerate(steps, start=1):
        try:
            opts = build_step_options(
                step, args.model, args.chunk_size, args.overlap, args.distance
            )
        except ValueError as e:
            print(f"ERROR: {e}")
            sys.exit(1)
        
        comma = "," if i < len(steps) else ""
        sql_parts.append(f"    step_{i} => '{step}',")
        sql_parts.append(f"    step_{i}_options => {opts}{comma}")
    
    sql_parts.append(");")
    return "\n".join(sql_parts)


def main():
    parser = argparse.ArgumentParser(description="AIDB pipeline SQL generator")
    parser.add_argument("--name", required=True, help="Pipeline name (max 46 chars)")
    parser.add_argument("--source", required=True, help="Source table or volume name")
    parser.add_argument("--source-key-column", help="Primary key column in source table")
    parser.add_argument("--source-data-column", help="Data column in source table")
    parser.add_argument("--destination", help="Destination table name (must not exist)")
    parser.add_argument(
        "--steps",
        nargs="+",
        required=True,
        metavar="STEP",
        help=f"Ordered list of steps. Valid: {', '.join(VALID_STEPS)}",
    )
    parser.add_argument("--model", help="Model name for embedding/OCR/summarization steps")
    parser.add_argument("--mode", choices=VALID_MODES, default="Disabled",
                        help="Auto-processing mode (default: Disabled)")
    parser.add_argument("--chunk-size", type=int, default=512,
                        help="Desired chunk size for ChunkText (default: 512)")
    parser.add_argument("--overlap", type=int, default=64,
                        help="Overlap for ChunkText (default: 64)")
    parser.add_argument("--distance", default="Cosine",
                        choices=["L2", "Cosine", "InnerProduct", "L1", "Hamming", "Jaccard"],
                        help="Distance operator for KnowledgeBase (default: Cosine)")
    
    args = parser.parse_args()
    
    # Validate name length
    if len(args.name) > 46:
        print(f"ERROR: Pipeline name '{args.name}' is {len(args.name)} characters. Maximum is 46.")
        sys.exit(1)
    
    # Validate mode
    if args.mode not in VALID_MODES:
        print(f"ERROR: Invalid mode '{args.mode}'. Valid: {', '.join(VALID_MODES)}")
        sys.exit(1)
    
    # Validate step count
    if len(args.steps) > 10:
        print(f"ERROR: Too many steps ({len(args.steps)}). Maximum is 10.")
        sys.exit(1)
    
    # Validate step sequence
    ok, msg = validate_sequence(args.steps)
    if not ok:
        print(f"ERROR: {msg}")
        sys.exit(1)
    
    print("-- Generated by aidb_pipeline_builder.py")
    print("-- Validate before running in production!")
    print()
    print(generate_sql(args))
    print()
    print("-- To run the pipeline manually (if mode=Disabled):")
    print(f"-- SELECT aidb.run_pipeline('{args.name}');")
    print()
    print("-- To check for errors:")
    print(f"-- SELECT * FROM aidb.list_pipelines() WHERE name = '{args.name}';")


if __name__ == "__main__":
    main()
