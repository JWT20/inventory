# Next Steps

- **Wine content validation for image matching**: Add a wine-content validation step that rejects images where Gemini says "this is not wine." The vision description already contains that signal — it just isn't being used. Currently, uploading a non-wine image (e.g. a keyboard) as a reference and then scanning a similar non-wine image produces a high-confidence match because the embeddings for two "this is not wine" descriptions are very similar to each other.
