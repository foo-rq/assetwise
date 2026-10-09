# Portfolio workspace prototype

Open `index.html` in a browser. No installation, network requests or build step is required. Reloading restores the example; nothing is persisted.

This is a design review, not the application foundation. It implements allocation editing, search across 4 assets, removal, cash, undo and proportional allocation previews. It includes no market feed, risk calculations, recommendation rankings, risk questionnaire or saved scenarios.

Visual revision: the holdings editor opens the allocation experiment in a dialog while analysis uses an open report layout. Verdana body text is 14px with 13px secondary text and bold numeric inputs. Touch inputs remain 16px. Fields share a white background and uniform borders, with a single inset focus outline. Data limitations use muted brown text on a pale background. The approved wordmark retains its original styling. The analysis explanation opens in a dialog without moving the page content. These are design proposals for review, not a completed visual-accessibility audit.

## Review steps

Latest picker revision: search results float below the input without moving the holdings table. The allocation picker uses the same dropdown style. Try arrow keys, Enter, Escape and Tab, plus clicking outside either picker. Confirmations remain for 2 seconds and fade over 150ms; errors do not expire. Colours now share a palette based on the wordmark.

1. Start with AAPL 70%, SPY 20%, cash 10%. Change AAPL to 60%; cash becomes 20% and SPY remains 20%.
2. Try AAPL at 90% while SPY is 20%. The edit is rejected with a message and the prior weight is restored.
3. Open Try an allocation and preview IEF at 15% from the starting example. Proposed weights are AAPL 59.50%, SPY 17%, IEF 15%, cash 8.50%. Current holdings stay unchanged until Apply change.
4. Apply and then Undo. The original allocation returns. Discard should leave current holdings unchanged.
5. Search for GLD, add it at 0%, remove it and undo the removal. Remove all holdings to inspect the cash-only state.
6. Use Tab and keyboard controls; check the layout at a narrow mobile width. Consider whether labels, weights and before/after comparisons are easy to read.
7. Open “Analysis limits”. The workspace should stay in place. Close with its button, then reopen and press Escape. Focus should return to the explanation button.

8. Open Try an allocation. Check field focus with the mouse and Tab. Preview a change, then close with Escape: holdings must remain unchanged. Reopen, preview and Apply: the dialog should close and Undo should receive focus.

## Design basis

- W3C, [Native modal dialogs](https://www.w3.org/WAI/WCAG22/Techniques/html/H102): informs the explanation dialog with a close button and keyboard focus handling.

- Nielsen Norman Group, [Similarity Principle](https://www.nngroup.com/articles/gestalt-similarity/): informs the different visual treatments for editable holdings and analysis results.
- GOV.UK Design System, [Text input](https://design-system.service.gov.uk/components/text-input/): informs visible field boundaries and input widths appropriate to the expected value.

- Nielsen Norman Group, [User Control and Freedom](https://www.nngroup.com/articles/user-control-and-freedom/): informs undo and a discardable preview.
- Nielsen Norman Group, [Visibility of System Status](https://www.nngroup.com/articles/visibility-system-status/): informs explicit prototype/data status and feedback after changes.
- W3C, [Forms Tutorial](https://www.w3.org/WAI/tutorials/forms/): informs labelled native controls and validation feedback.
- W3C, [Tables](https://design-system.w3.org/styles/tables.html): informs semantic column/row headers and captions.

The palette and layout are design proposals, not claims that these sources endorse this particular interface. This prototype has not undergone a full accessibility audit.
