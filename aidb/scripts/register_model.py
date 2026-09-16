#!/usr/bin/env python3
"""
AIDB Model Registration Helper
Generates SQL for registering AI models with AIDB.

Usage:
    python3 register_model.py --provider openai_embeddings --name my_embed \
        --model-id text-embedding-3-small --api-key sk-...

    python3 register_model.py --provider bert_local --name my_bert \
        --model-path /path/to/bert-weights

    python3 register_model.py --provider dummy --name test_model

    python3 register_model.py --provider completions --name my_llm \
        --model-id mistral-7b --url http://vllm-host:8000/v1/chat/completions

Supported providers:
  Embedding:     openai_embeddings, embeddings, bert_local, clip_local,
                 hf_tei, nim_embeddings, openrouter_embeddings, llamacpp_embeddings
  Completion:    openai_completions, completions, llama_instruct_local,
                 smollm2_local, gemini, openrouter_chat, nim_completions,
                 llamacpp_generate
  Agent-capable: anthropic_messages, anthropic_messages_azure,
                 anthropic_messages_bedrock, openai_responses,
                 openai_responses_azure
  OCR:           nim_paddle_ocr, llamacpp_ocr
  Reranking:     hf_tei_reranking, nim_reranking, llamacpp_reranking
  Test:          dummy
"""

import argparse
import json
import sys

EMBEDDING_PROVIDERS = {
    "openai_embeddings", "embeddings", "bert_local", "clip_local",
    "hf_tei", "nim_embeddings", "openrouter_embeddings", "t5_local",
    "llamacpp_embeddings"
}

COMPLETION_PROVIDERS = {
    "openai_completions", "completions", "llama_instruct_local",
    "smollm2_local", "gemini", "openrouter_chat", "nim_completions",
    "t5_local", "llamacpp_generate",
    "anthropic_messages", "anthropic_messages_azure",
    "anthropic_messages_bedrock", "openai_responses", "openai_responses_azure"
}

AGENT_CAPABLE_PROVIDERS = {
    "anthropic_messages", "anthropic_messages_azure",
    "anthropic_messages_bedrock", "openai_responses", "openai_responses_azure"
}

REMOTE_PROVIDERS = {
    "openai_embeddings", "embeddings", "openai_completions", "completions",
    "hf_tei", "hf_tei_reranking", "nim_embeddings", "nim_completions",
    "nim_clip", "nim_paddle_ocr", "nim_reranking", "gemini",
    "openrouter_chat", "openrouter_embeddings",
    "anthropic_messages", "anthropic_messages_azure",
    "anthropic_messages_bedrock", "openai_responses", "openai_responses_azure"
}

LOCAL_PROVIDERS = {
    "bert_local", "clip_local", "t5_local", "llama_instruct_local",
    "smollm2_local", "llamacpp_embeddings", "llamacpp_generate",
    "llamacpp_reranking", "llamacpp_ocr"
}

OCR_PROVIDERS = {"nim_paddle_ocr", "llamacpp_ocr"}
RERANKING_PROVIDERS = {"hf_tei_reranking", "nim_reranking", "llamacpp_reranking"}


def build_config(args):
    provider = args.provider

    if provider == "dummy":
        return None, None

    config = {}
    credentials = {}

    if args.model_id:
        config["model"] = args.model_id

    if args.model_path:
        config["model"] = args.model_path

    if args.url:
        config["url"] = args.url

    if args.n_ctx:
        config["n_ctx"] = args.n_ctx

    if args.api_key:
        if provider in ("gemini",):
            config["api_key"] = args.api_key
        else:
            credentials["api_key"] = args.api_key

    return config or None, credentials or None


def generate_sql(args):
    config, credentials = build_config(args)

    config_str = "NULL"
    creds_str = "NULL"

    if config:
        if args.provider in EMBEDDING_PROVIDERS and args.provider not in LOCAL_PROVIDERS:
            parts = []
            if config.get("model"):
                parts.append(f"model => '{config['model']}'")
            if config.get("url"):
                parts.append(f"url => '{config['url']}'")
            config_str = f"aidb.embeddings_config({', '.join(parts)})"
        elif args.provider in (COMPLETION_PROVIDERS | AGENT_CAPABLE_PROVIDERS) and args.provider not in LOCAL_PROVIDERS:
            parts = []
            if config.get("model"):
                parts.append(f"model => '{config['model']}'")
            if config.get("url"):
                parts.append(f"url => '{config['url']}'")
            config_str = f"aidb.completions_config({', '.join(parts)})"
        else:
            config_str = f"'{json.dumps(config)}'::JSONB"

    if credentials:
        creds_str = f"'{json.dumps(credentials)}'::JSONB"

    validate_flag = ""
    if args.provider in LOCAL_PROVIDERS:
        validate_flag = ",\n    validate     => false  -- skip download/path check on registration"

    sql = f"""-- Register model '{args.name}' with provider '{args.provider}'
SELECT aidb.create_model(
    name        => '{args.name}',
    provider    => '{args.provider}'"""

    if config_str != "NULL":
        sql += f",\n    config      => {config_str}"

    if creds_str != "NULL":
        sql += f",\n    credentials => {creds_str}"

    sql += validate_flag
    sql += "\n);\n"

    # Usage hints
    if args.provider in AGENT_CAPABLE_PROVIDERS:
        sql += f"""
-- This is an AGENT-CAPABLE model (supports tool-calling).
-- Use it in aidb.create_agent():
-- SELECT aidb.create_agent(name => 'my_agent', instructions => '...', model => '{args.name}', tools => ARRAY['my_tool']);
-- SELECT conversation_id, message, error FROM aidb.agent_converse('my_agent', 'Hello!');
"""
    elif args.provider in EMBEDDING_PROVIDERS:
        sql += f"""
-- Test the model (returns a VECTOR):
-- SELECT aidb.encode_text('hello world', '{args.name}');
-- SELECT aidb.encode_text_batch(ARRAY['text1', 'text2'], '{args.name}');
-- Get embedding dimensions:
-- SELECT aidb.get_adapter_embedding_dimensions('{args.name}');
"""
    elif args.provider in COMPLETION_PROVIDERS:
        sql += f"""
-- Test the model:
-- SELECT aidb.summarize_text('your long text here', aidb.summarize_text_config(model => '{args.name}'));
"""
    elif args.provider in OCR_PROVIDERS:
        sql += f"""
-- Use in a PdfToImage -> PerformOcr pipeline:
-- step_2 => 'PerformOcr',
-- step_2_options => aidb.ocr_config('{args.name}')
"""

    if creds_str != "NULL":
        sql += """
-- NOTE: Credentials are stored securely in pg_user_mappings.
-- They will NOT appear in aidb.list_models() or aidb.get_model() output.
"""

    return sql


def main():
    parser = argparse.ArgumentParser(
        description="Generate SQL to register an AIDB model",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("--name", required=True, help="Unique model name in AIDB")
    parser.add_argument("--provider", required=True, help="Provider type (see list above)")
    parser.add_argument("--model-id", help="Model identifier within the provider (e.g., gpt-4o)")
    parser.add_argument("--model-path", help="Path to local model weights (local providers)")
    parser.add_argument("--api-key", help="API key (stored in credentials, not config)")
    parser.add_argument("--url", help="Custom endpoint URL (for generic/self-hosted providers)")
    parser.add_argument("--n-ctx", type=int, help="Context window size (for llamacpp providers)")

    args = parser.parse_args()
    print(generate_sql(args))


if __name__ == "__main__":
    main()
