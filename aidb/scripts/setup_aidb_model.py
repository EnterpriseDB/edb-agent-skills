#!/usr/bin/env python3
"""
AIDB model registration helper.

Generates and optionally executes the SQL to register one AI model with AIDB.
Supports the most common providers. Use --dry-run to preview the SQL only.

Usage:
    python3 setup_aidb_model.py \\
        --model-name my_embedder \\
        --provider openai_embeddings \\
        --model-id text-embedding-3-small \\
        --api-key sk-... \\
        [--dry-run] \\
        [--host HOST] [--port PORT] [--dbname DB] [--user USER] [--password PW]

Requires: psycopg2 (pip install psycopg2-binary)
"""

import argparse
import json
import sys
import textwrap

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    print("ERROR: psycopg2 is required. Install it with: pip install psycopg2-binary", file=sys.stderr)
    sys.exit(2)

PROVIDER_NOTES = {
    "openai_embeddings": "OpenAI /v1/embeddings. --model-id required (e.g. text-embedding-3-small).",
    "openai_completions": "OpenAI /v1/chat/completions. --model-id required (e.g. gpt-4o-mini).",
    "openai_responses": "OpenAI Responses API. --model-id required.",
    "openai_responses_azure": "OpenAI Responses API via Azure. --model-id and --url required (e.g. https://<resource>.openai.azure.com/openai/v1/responses).",
    "anthropic_messages": "Anthropic Messages API. --model-id required (e.g. claude-3-5-sonnet-20241022).",
    "anthropic_messages_azure": "Anthropic via Azure. --model-id and --url required.",
    "anthropic_messages_bedrock": "Anthropic via AWS Bedrock. --url required (regional base, e.g. https://bedrock-runtime.us-east-1.amazonaws.com).",
    "completions": "Any OpenAI-compatible completions endpoint. --url required (full endpoint, e.g. https://host/v1/chat/completions). --model-id required.",
    "embeddings": "Any OpenAI-compatible embeddings endpoint. --url required (full endpoint). --model-id required.",
    "nim_embeddings": "NVIDIA NIM embeddings. --api-key required (nvapi-...).",
    "nim_completions": "NVIDIA NIM completions. --api-key required.",
    "nim_clip": "NVIDIA NIM CLIP (image+text). --api-key required.",
    "nim_reranking": "NVIDIA NIM reranking. --api-key required.",
    "nim_paddle_ocr": "NVIDIA NIM Paddle OCR. --api-key required.",
    "gemini": "Google Gemini. --api-key required. Default model: gemini-2.5-flash.",
    "bert_local": "BERT in-process from HuggingFace. --model-id = HF repo (default: sentence-transformers/all-MiniLM-L6-v2). No API key.",
    "clip_local": "CLIP in-process. --model-id = HF repo (default: openai/clip-vit-base-patch32). No API key.",
    "llama_instruct_local": "Llama instruction-tuned in-process. --model-id = HF repo (default: TinyLlama/TinyLlama-1.1B-Chat-v1.0). No API key.",
    "llamacpp_generate": "GGUF completions in-process. --model-id = HF repo, --model-file = .gguf filename.",
    "llamacpp_embeddings": "GGUF embeddings in-process. --model-id = HF repo, --model-file = .gguf filename.",
    "hf_tei": "HuggingFace TEI embeddings server. --url = base URL (aidb appends /embed). No model key.",
    "hf_tei_reranking": "HuggingFace TEI reranking server. --url = base URL.",
    "openrouter_chat": "OpenRouter chat. --api-key and --model-id required.",
    "openrouter_embeddings": "OpenRouter embeddings. --api-key and --model-id required.",
    "dummy": "Built-in test provider. No API key, no server. Returns fixed embeddings [0.1,0.2,0.3] and 'Hello world!'.",
}


def build_config(args):
    """Build the config JSONB dict for the given provider."""
    p = args.provider
    cfg = {}

    if p in ("openai_embeddings", "embeddings"):
        if args.model_id:
            cfg["model"] = args.model_id
        if args.url:
            cfg["url"] = args.url

    elif p in ("openai_completions", "completions", "openai_responses", "openai_responses_azure",
               "anthropic_messages", "anthropic_messages_azure", "anthropic_messages_bedrock",
               "nim_embeddings", "nim_completions", "nim_clip", "nim_reranking", "nim_paddle_ocr"):
        if args.model_id:
            cfg["model"] = args.model_id
        if args.url:
            cfg["url"] = args.url

    elif p == "gemini":
        if args.model_id:
            cfg["model"] = args.model_id
        if args.url:
            cfg["url"] = args.url

    elif p in ("bert_local", "clip_local", "llama_instruct_local"):
        if args.model_id:
            cfg["hf_model"] = args.model_id

    elif p in ("llamacpp_generate", "llamacpp_embeddings", "llamacpp_reranking", "llamacpp_ocr"):
        if args.model_id:
            cfg["hf_model"] = args.model_id
        if args.model_file:
            cfg["model_file"] = args.model_file

    elif p in ("hf_tei", "hf_tei_reranking"):
        if args.url:
            cfg["url"] = args.url

    elif p in ("openrouter_chat", "openrouter_embeddings"):
        if args.model_id:
            cfg["model"] = args.model_id
        if args.url:
            cfg["url"] = args.url

    return cfg


def build_sql(args):
    """Return the SQL statement to register the model."""
    cfg = build_config(args)
    config_sql = json.dumps(cfg)
    creds_sql = None
    if args.api_key:
        creds_sql = json.dumps({"api_key": args.api_key})

    validate = "false" if args.no_validate else "true"

    parts = [
        f"SELECT aidb.create_model(",
        f"    name       => {_ql(args.model_name)},",
        f"    provider   => {_ql(args.provider)},",
        f"    config     => {_ql(config_sql)}::jsonb,",
    ]
    if creds_sql:
        parts.append(f"    credentials => {_ql(creds_sql)}::jsonb,")
    parts.append(f"    validate   => {validate}")
    parts.append(f");")
    return "\n".join(parts)


def _ql(s):
    """Dollar-quote a string to avoid escaping issues."""
    return f"$${s}$$"


def main():
    parser = argparse.ArgumentParser(
        description="Generate (and optionally run) SQL to register an AIDB model.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Supported providers:\n" + "\n".join(
            f"  {p:<30} {note}" for p, note in PROVIDER_NOTES.items()
        ),
    )
    parser.add_argument("--model-name", required=True, help="Name to register the model under in AIDB")
    parser.add_argument(
        "--provider",
        required=True,
        choices=list(PROVIDER_NOTES.keys()),
        help="AIDB provider name",
    )
    parser.add_argument("--model-id", help="Model identifier (HF repo, OpenAI model name, etc.)")
    parser.add_argument("--api-key", help="API key (stored in credentials, not config)")
    parser.add_argument("--url", help="Endpoint URL (for OpenAI-compatible / NIM / TEI providers)")
    parser.add_argument("--model-file", help="GGUF filename (for llamacpp_* providers)")
    parser.add_argument("--no-validate", action="store_true", help="Register without probing the model endpoint")
    parser.add_argument("--dry-run", action="store_true", help="Print SQL only, do not connect to PostgreSQL")
    parser.add_argument("--host", help="PostgreSQL host (default: PGHOST or libpq default)")
    parser.add_argument("--port", type=int, help="PostgreSQL port (default: PGPORT or 5432)")
    parser.add_argument("--dbname", help="Database name (default: PGDATABASE or user name)")
    parser.add_argument("--user", help="PostgreSQL user (default: PGUSER)")
    parser.add_argument("--password", help="Password (default: PGPASSWORD or ~/.pgpass)")
    args = parser.parse_args()

    sql = build_sql(args)
    print("-- Generated SQL:")
    print(sql)

    if args.dry_run:
        print("\n-- Dry run: not executing. Remove --dry-run to run against a live database.")
        sys.exit(0)

    # Connect and run
    try:
        params = {k: v for k, v in {
            "host": args.host,
            "port": args.port,
            "dbname": args.dbname,
            "user": args.user,
            "password": args.password,
        }.items() if v is not None}
        conn = psycopg2.connect(**params)
        conn.autocommit = True
    except Exception as e:
        print(f"\nERROR: Could not connect: {e}", file=sys.stderr)
        sys.exit(2)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(sql)
            rows = cur.fetchall()
            if rows:
                print("\n-- Result:")
                for row in rows:
                    print(dict(row))
            else:
                print(f"\n-- Model '{args.model_name}' registered successfully.")
        except Exception as e:
            print(f"\nERROR: {e}", file=sys.stderr)
            sys.exit(1)

    conn.close()


if __name__ == "__main__":
    main()
