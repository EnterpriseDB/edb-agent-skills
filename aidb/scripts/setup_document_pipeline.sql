-- setup_document_pipeline.sql
-- Template: PDF/HTML ingestion pipeline with OCR or text extraction
-- Produces full text output per document page/chunk.

-- ==========================================
-- Option A: PDF → OCR text (image-based PDFs)
-- Requires nim_paddle_ocr model
-- ==========================================
SELECT aidb.create_model(
    'my_nim_ocr',
    'nim_paddle_ocr',
    credentials => '{"api_key": "<YOUR_NIM_API_KEY>"}'::JSONB
);

CREATE TABLE IF NOT EXISTS pdf_docs (
    id SERIAL PRIMARY KEY,
    content BYTEA NOT NULL
);

SELECT aidb.create_pipeline(
    name              => 'pdf_ocr_pipeline',
    source            => 'pdf_docs',
    source_key_column => 'id',
    source_data_column => 'content',
    auto_processing   => 'Background',
    step_1            => 'PdfToImage',
    step_1_options    => '{"dpi": 300, "format": {"type": "png"}, "render_annotations": true}'::JSONB,
    step_2            => 'PerformOcr',
    step_2_options    => aidb.ocr_config('my_nim_ocr')
);

-- ==========================================
-- Option B: HTML → Text → Chunk → Embed
-- ==========================================
-- CREATE TABLE IF NOT EXISTS html_docs (
--     id SERIAL PRIMARY KEY,
--     content BYTEA NOT NULL
-- );
--
-- SELECT aidb.create_pipeline(
--     name               => 'html_embed_pipeline',
--     source             => 'html_docs',
--     source_key_column  => 'id',
--     source_data_column => 'content',
--     auto_processing    => 'Background',
--     step_1             => 'ParseHtml',
--     step_1_options     => aidb.html_parse_config(method => 'StructuredMarkdown'),
--     step_2             => 'ChunkText',
--     step_2_options     => aidb.chunk_text_config(desired_length => 512, overlap_length => 64),
--     step_3             => 'KnowledgeBase',
--     step_3_options     => aidb.knowledge_base_config(
--         model             => 'my_embed_model',
--         data_format       => 'Text',
--         distance_operator => 'Cosine'
--     )
-- );

-- Run manually:
-- SELECT aidb.run_pipeline('pdf_ocr_pipeline');

-- Cleanup:
-- SELECT aidb.delete_pipeline('pdf_ocr_pipeline', cascade => true);
