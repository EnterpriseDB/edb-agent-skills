#!/usr/bin/env python3
"""
AIDB Pipeline Setup Helper
Generates boilerplate SQL for creating common AIDB pipeline patterns.

Usage:
    python3 setup_pipeline.py --type text_embedding --pipeline-name my_pipe \
        --source my_table --data-col content --model my_embed_model

    python3 setup_pipeline.py --type pdf_ocr --pipeline-name pdf_pipe \
        --source docs_table --data-col pdf_bytes --model my_ocr_model

    python3 setup_pipeline.py --type semantic_kb --kb-name my_kb \
        --model my_embed_model --schemas public,sales

Supported pipeline types:
    text_embedding   - ChunkText -> KnowledgeBase
    pdf_to_text      - ParsePdf -> ChunkText -> KnowledgeBase
    pdf_ocr          - PdfToImage -> PerformOcr
    html_to_embed    - ParseHtml -> ChunkText -> KnowledgeBase
    summarize        - SummarizeText
    semantic_kb      - Creates a Semantic Knowledge Base (not a pipeline)
"""

import argparse
import sys


def generate_text_embedding(args):
    return f"""-- Create a text embedding pipeline: ChunkText -> KnowledgeBase
-- Prerequisites: model '{args.model}' must be registered as an embedding model.

-- Step 1: Register an embedding model (skip if already registered)
-- SELECT aidb.create_model(
--     '{args.model}',
--     'openai_embeddings',  -- or 'bert_local', 'hf_tei', 'llamacpp_embeddings', etc.
--     config => aidb.embeddings_config(model => 'text-embedding-3-small', api_key => 'sk-...')
-- );

-- Step 2: Create the pipeline
SELECT aidb.create_pipeline(
    name                => '{args.pipeline_name}',
    source              => '{args.source}',
    source_key_column   => '{args.key_col}',
    source_data_column  => '{args.data_col}',
    auto_processing     => '{args.mode}',  -- 'Live', 'Background', or 'Disabled'
    step_1              => 'ChunkText',
    step_1_options      => aidb.chunk_text_config(
        desired_length  => {args.chunk_size},
        overlap_length  => {args.chunk_overlap}
    ),
    step_2              => 'KnowledgeBase',
    step_2_options      => aidb.knowledge_base_config(
        model             => '{args.model}',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);

-- Step 3: Run the pipeline (required for Disabled mode or initial load)
-- SELECT aidb.run_pipeline('{args.pipeline_name}');

-- Step 4: Similarity search against the destination table (named pipeline_<name> by default)
-- SELECT source_id, embedding <=> aidb.encode_text_query('your query', '{args.model}') AS distance
-- FROM pipeline_{args.pipeline_name}
-- ORDER BY embedding <=> aidb.encode_text_query('your query', '{args.model}')
-- LIMIT 10;
"""


def generate_pdf_ocr(args):
    return f"""-- Create a PDF -> OCR pipeline: PdfToImage -> PerformOcr
-- Prerequisites:
--   1. OCR model '{args.model}' registered with 'nim_paddle_ocr' provider.
--   2. Source table '{args.source}' has a BYTEA column '{args.data_col}'.

-- Step 1: Register OCR model (skip if already registered)
-- SELECT aidb.create_model(
--     '{args.model}',
--     'nim_paddle_ocr',
--     credentials => '{{"api_key": "your_nim_api_key"}}'::JSONB
-- );

-- Step 2: Create the PDF -> Image -> OCR pipeline
SELECT aidb.create_pipeline(
    name                => '{args.pipeline_name}',
    source              => '{args.source}',
    source_key_column   => '{args.key_col}',
    source_data_column  => '{args.data_col}',
    auto_processing     => '{args.mode}',
    step_1              => 'PdfToImage',
    step_1_options      => '{{
        "dpi": 300,
        "format": {{"type": "png"}},
        "render_annotations": true,
        "max_pages": null
    }}'::JSONB,
    step_2              => 'PerformOcr',
    step_2_options      => aidb.ocr_config('{args.model}')
);

-- Step 3: Run the pipeline
-- SELECT aidb.run_pipeline('{args.pipeline_name}');

-- Step 4: Query extracted text
-- SELECT source_id, part_ids, value
-- FROM pipeline_{args.pipeline_name}
-- ORDER BY source_id, part_ids;
"""


def generate_pdf_to_text(args):
    return f"""-- Create a PDF -> Text Embedding pipeline: ParsePdf -> ChunkText -> KnowledgeBase
-- Source column '{args.data_col}' must contain PDF bytes (BYTEA).

SELECT aidb.create_pipeline(
    name                => '{args.pipeline_name}',
    source              => '{args.source}',
    source_key_column   => '{args.key_col}',
    source_data_column  => '{args.data_col}',
    auto_processing     => '{args.mode}',
    step_1              => 'ParsePdf',
    step_1_options      => aidb.pdf_parse_config(
        method                => 'Structured',
        allow_partial_parsing => TRUE
    ),
    step_2              => 'ChunkText',
    step_2_options      => aidb.chunk_text_config(
        desired_length  => {args.chunk_size},
        overlap_length  => {args.chunk_overlap}
    ),
    step_3              => 'KnowledgeBase',
    step_3_options      => aidb.knowledge_base_config(
        model             => '{args.model}',
        data_format       => 'Text',
        distance_operator => 'Cosine',
        vector_index      => aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
    )
);

-- SELECT aidb.run_pipeline('{args.pipeline_name}');

-- Similarity search:
-- SELECT source_id, embedding <=> aidb.encode_text_query('your query', '{args.model}') AS distance
-- FROM pipeline_{args.pipeline_name}
-- ORDER BY distance
-- LIMIT 10;
"""


def generate_html_embed(args):
    return f"""-- Create an HTML -> Text Embedding pipeline: ParseHtml -> ChunkText -> KnowledgeBase
-- Source column '{args.data_col}' must contain HTML bytes (BYTEA).

SELECT aidb.create_pipeline(
    name                => '{args.pipeline_name}',
    source              => '{args.source}',
    source_key_column   => '{args.key_col}',
    source_data_column  => '{args.data_col}',
    auto_processing     => '{args.mode}',
    step_1              => 'ParseHtml',
    step_1_options      => aidb.html_parse_config(method => 'StructuredMarkdown'),
    step_2              => 'ChunkText',
    step_2_options      => aidb.chunk_text_config(
        desired_length  => {args.chunk_size},
        overlap_length  => {args.chunk_overlap}
    ),
    step_3              => 'KnowledgeBase',
    step_3_options      => aidb.knowledge_base_config(
        model             => '{args.model}',
        data_format       => 'Text',
        distance_operator => 'Cosine'
    )
);

-- SELECT aidb.run_pipeline('{args.pipeline_name}');
"""


def generate_summarize(args):
    return f"""-- Create a text summarization pipeline: SummarizeText
-- Prerequisites: completion model '{args.model}' must be registered.

SELECT aidb.create_pipeline(
    name                => '{args.pipeline_name}',
    source              => '{args.source}',
    source_key_column   => '{args.key_col}',
    source_data_column  => '{args.data_col}',
    auto_processing     => '{args.mode}',
    step_1              => 'SummarizeText',
    step_1_options      => aidb.summarize_text_config(
        model            => '{args.model}',
        strategy         => 'reduce',
        reduction_factor => 3,
        inference_config => aidb.inference_config(temperature => 0.3)
    )
);

-- SELECT aidb.run_pipeline('{args.pipeline_name}');
"""


def generate_semantic_kb(args):
    schemas_arr = "ARRAY[" + ", ".join(f"'{s.strip()}'" for s in args.schemas.split(",")) + "]"
    return f"""-- Create a Semantic Knowledge Base over schema(s): {args.schemas}
-- This indexes all tables, views, columns, and comments for natural-language search.
-- Use for text-to-SQL, schema discovery, and semantic alias lookup.

SELECT aidb.create_semantic_kb(
    name            => '{args.kb_name}',
    model           => '{args.model}',
    schemas         => {schemas_arr},
    auto_processing => 'Live',   -- 'Live' | 'Background' | 'Disabled'
    bypass_triggers => FALSE,
    vector_index    => NULL      -- or aidb.vector_index_hnsw_config(m => 16, ef_construction => 64)
);

-- Search for tables matching a concept:
-- SELECT schema_name, relation_name, entity_type, similarity
-- FROM aidb.get_tables('{args.kb_name}', 'customer orders', 0.7, 10, 0);

-- Search for columns matching a concept:
-- SELECT relation_name, column_name, definition, similarity
-- FROM aidb.get_columns('{args.kb_name}', 'email address', 0.7, 10, 0);

-- Combined search (tables + columns + aliases, ranked):
-- SELECT source_type, entity_type, relation_name, column_name, score
-- FROM aidb.semantic_kb_search('who stores order totals', '{args.kb_name}', 10)
-- ORDER BY rank;

-- Refresh manually (if Disabled mode):
-- SELECT aidb.refresh_semantic_kb('{args.kb_name}');

-- Check stats:
-- SELECT * FROM aidb.semantic_kb_stats('{args.kb_name}');
"""


def main():
    parser = argparse.ArgumentParser(
        description="Generate AIDB pipeline SQL boilerplate",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--type", required=True,
                        choices=["text_embedding", "pdf_to_text", "pdf_ocr",
                                 "html_to_embed", "summarize", "semantic_kb"],
                        help="Pipeline type to generate")
    parser.add_argument("--pipeline-name", default="my_pipeline",
                        help="Name for the pipeline (max 46 chars)")
    parser.add_argument("--kb-name", default="my_kb",
                        help="Name for the semantic KB (semantic_kb type only)")
    parser.add_argument("--source", default="my_source_table",
                        help="Source table or volume name")
    parser.add_argument("--data-col", default="content",
                        help="Source data column name")
    parser.add_argument("--key-col", default="id",
                        help="Source primary key column name")
    parser.add_argument("--model", default="my_model",
                        help="Registered model name")
    parser.add_argument("--mode", default="Background",
                        choices=["Live", "Background", "Disabled"],
                        help="Pipeline auto_processing mode")
    parser.add_argument("--chunk-size", type=int, default=512,
                        help="Chunk desired length in characters")
    parser.add_argument("--chunk-overlap", type=int, default=64,
                        help="Chunk overlap in characters")
    parser.add_argument("--schemas", default="public",
                        help="Comma-separated schema names (semantic_kb only)")

    args = parser.parse_args()

    # Validate pipeline name length
    name = args.pipeline_name if args.type != "semantic_kb" else args.kb_name
    if len(name) > 46:
        print(f"ERROR: Pipeline/KB name '{name}' exceeds 46-character limit ({len(name)} chars).", file=sys.stderr)
        sys.exit(1)

    generators = {
        "text_embedding": generate_text_embedding,
        "pdf_to_text":    generate_pdf_to_text,
        "pdf_ocr":        generate_pdf_ocr,
        "html_to_embed":  generate_html_embed,
        "summarize":      generate_summarize,
        "semantic_kb":    generate_semantic_kb,
    }

    print(generators[args.type](args))


if __name__ == "__main__":
    main()
