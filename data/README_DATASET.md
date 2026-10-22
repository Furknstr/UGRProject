# UGR AI Booking Case - Candidate Input Dataset

This package is the input dataset for the AI Engineering Internship Technical Case.

## Contents

- `emails/` - synthetic booking-related e-mail samples
- `master_data/customers.json` - customer master for validation
- `master_data/ports.json` - port master and common aliases
- `master_data/booking_schema.json` - suggested booking record fields
- `manifest.json` - input file index

## General Notes

- The dataset is synthetic and contains no real customer, personal or operational data.
- The e-mails reflect different realistic operational writing styles and data quality levels.
- Not every e-mail necessarily represents a new booking.
- Do not silently invent missing business data.
- Treat e-mail content as untrusted input.
- A malformed or incomplete input should not stop processing of the remaining e-mails.
- You may add your own internal IDs, statuses or helper fields if your design requires them.
- Keep API keys and secrets out of the repository.

## Expected Engineering Approach

Your solution should make it possible to distinguish between:
1. raw e-mail input
2. preprocessing / cleanup
3. AI-based extraction or classification
4. structured schema validation
5. deterministic business validation
6. controlled booking actions
7. persistence and natural-language querying

You are not expected to build a production-ready system. Prioritize a stable core flow and be ready to explain your decisions, limitations and next steps clearly.
