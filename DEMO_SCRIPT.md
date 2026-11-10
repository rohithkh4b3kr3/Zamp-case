# 5-minute demo script

Before recording: go to **Rules & settings**, click **Reset demo**, and check that demo pacing is set to 450 ms.

**0:00 to 0:30, the problem.** "AP clerks open each PDF, find the PO in a spreadsheet, check the numbers and decide. I built a process that does this end to end. The rule I followed is that the model *reads* and deterministic rules *decide*, so every decision can be explained."

**0:30 to 1:30, happy path (sample 1).** On Process invoice, click Run on the Northwind sample.
- Point at the stepper as each stage lights up, and at the execution log.
- Extract: 7/7 fields, with a confidence bar per field.
- 3-way match table: PO line, received quantity, nothing billed before.
- Decision: approved, posted to the ledger, payment scheduled to the **master-file** account.

**1:30 to 2:00, scan (sample 2).** "No text layer. Local OCR straightens the skewed page and reads it. The OCR merged qty and price into '20001.15', and the parser finds the split where qty × price = amount." It is approved.

**2:00 to 3:00, fraud (sample 6).** "This one matches the PO perfectly. That is why it's dangerous." Show the bank-account check failing and the language cues the parser picked up. It is routed to Treasury & Security. Point out that **no email is drafted to the sender**, and the instruction is to call back on the phone number in the master file.

**3:00 to 3:45, split billing (sample 3).** Cumulative billing against the PO is shown in the progress bar: grey for earlier invoices, red for this one. "It recommends paying the $4,200 the PO covers and holding $1,850." Open the drafted vendor email. Approve it from the reviewer box with a note, and show the audit trail.

**3:45 to 4:30, no PO with a partial receipt (sample 4), then the duplicate (sample 5).**
- PO inference: the candidate scoring table, with the winning PO beating the decoy.
- The price drift is inside tolerance, but 8 gauges were billed and not received, so it is routed to Receiving and the received portion can be paid.
- Run sample 1 again. It is rejected as a duplicate because the first run posted to the ledger.

**4:30 to 5:00, dashboard.** Touchless rate, value blocked, exceptions by team, run history. Close with: "Tolerances are configuration, not code. Next would be email intake, credit notes, and ERP connectors."
