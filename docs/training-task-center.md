# Common training and saved task center

The ten model-family workbenches share preparation, runtime budget/priority/queue controls and job-state guidance. Execution controls apply to the selected local or server execution identity. A disconnected job retains uncertain reservations until exit/release evidence is confirmed; reconnect observes the same execution rather than starting a replacement. Runtime limits request cancellation and do not guarantee immediate process exit during an outage.

Saved core/patch jobs remain discoverable in the scoped durable ledger after restart. Selecting a historical core job displays its exact saved state independently from an active training store. Failed patch/rotation preparation does not hide the job's cause when prepared data is absent; preparing valid data is required before another start. A missing explicit job/model/result cannot silently select the newest one. Completed candidates and continuation parents use compatible scoped selectors; imported weights and model quality still require their own qualification.

Project, source, labelset, account/API and compute identity bind task handoffs. Authority changes invalidate retained preparation imports, task actions and late replies before another render. Read-only recovery never launches a worker.

## Verification boundary

Current actual Chrome and macOS development Electron qualification opens all ten controlled interrupted records and checks common readiness/budget controls and reload. Separate current suites check parent-weight lineage and cancellation/result handoffs. Controlled network responses are identified explicitly. Saved records are not proof of training, GPU execution or quality; global cross-executor fairness, representative model quality, installed target and independent acceptance remain separate. Windows tests are excluded by the user.
