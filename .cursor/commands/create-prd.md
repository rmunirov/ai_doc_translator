# Create PRD: Generate Product Requirements Document

## Overview

Generate a concise, actionable Product Requirements Document (PRD) based on the current conversation context and project information. Write it to `$ARGUMENTS` (default: `PRD.md`).

## PRD Structure

Create a well-structured PRD with the following sections. Adapt depth and detail based on available information. Keep it under 300 lines — no fluff.

---

### Required Sections

**1. Executive Summary**
- What the product does (2-3 sentences)
- Core value proposition
- MVP goal

**2. Mission**
- One-sentence mission
- 3-5 core principles (bullet list)

**3. Target Users**
- Who uses this and why
- Technical level
- Key pain points / needs

**4. MVP Scope**
- **✅ In Scope** — grouped by category (Core, Technical, Integrations)
- **❌ Out of Scope** — features explicitly deferred

**5. User Stories**
- 4-6 stories: "As a [user], I want [action], so that [benefit]"
- Each story includes a concrete example

**6. Core Architecture & Patterns**
- High-level architecture (text diagram or list)
- Key design patterns used
- Data flow overview

**7. Technology Stack**
- Table: Component | Technology | Version
- Note primary vs optional dependencies

**8. Security & Configuration**
- Auth approach (or explicit "no auth for MVP")
- Key environment variables (`.env` template)
- Security scope (in/out)

**9. API Specification** (if applicable)
- Key endpoints: method, path, request, response
- Use JSON code blocks for payloads

**10. Success Criteria**
- ✅ Functional requirements (checkboxes)
- Quality / performance targets (numbers where possible)

**11. Implementation Phases**
- 3-4 phases with: Goal, Deliverables (✅ list), Validation criterion
- Realistic time estimate per phase

**12. Risks & Mitigations**
- 3-5 risks with specific mitigation strategy each

**13. Future Considerations**
- Post-MVP features (bullet list, no details needed)

---

## Instructions

### Step 1 — Extract context
- Read the entire conversation and any open/referenced files
- Identify: tech stack, core features, target users, constraints
- Note what is explicitly out of scope

### Step 2 — Fill sections
- Use concrete specifics from the conversation (real tech names, real constraints)
- For missing details: make reasonable assumptions and note them in the document
- Keep language professional and action-oriented

### Step 3 — Write the file
- Write to the path specified in `$ARGUMENTS` (default: `PRD.md`)
- Use markdown formatting throughout
- Keep total length under 300 lines for scannability

### Step 4 — Confirm
After writing:
1. State the file path
2. List any assumptions made
3. Suggest one next step (e.g., review Phase 1 scope)

---

## Style Guidelines

- **Tone**: direct, no marketing language
- **Checkboxes**: ✅ for in-scope, ❌ for out-of-scope
- **Tables**: use for tech stack, API endpoints
- **Code blocks**: use for `.env` examples, API payloads, architecture diagrams
- **Length**: concise — if a section has nothing meaningful to say, omit it

## Quality Checklist

Before saving the file, verify:
- ✅ All 13 sections present (or explicitly omitted with reason)
- ✅ MVP scope clearly separates what's in vs out
- ✅ User stories have a concrete example each
- ✅ Success criteria are measurable (numbers, not "good UX")
- ✅ Implementation phases are actionable and time-estimated
- ✅ Tech stack matches what was discussed in conversation
- ✅ No section repeats information from another section
