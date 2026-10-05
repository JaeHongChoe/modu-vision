# PLC and MES delivery contracts

The runtime settings panel saves Modbus TCP and HTTP MES mappings per project.
Saving a configuration does not restart a worker or contact a receiver. Stop and
start the owned inspection service explicitly to apply it.

## Modbus TCP

Configure distinct result, ACK, optional sequence and trigger registers, unit,
host, port, response timeout, ACK timeout and OK/NG/REVIEW values. TCP frames use
network byte order. `byte_order` controls only the unsigned 16-bit verdict and
sequence payload: `big` preserves the value; `little` exchanges its two bytes.
32-bit/float word order is not supported. A trigger can select one existing,
unlinked image inside the current project's source dataset. It does not choose
an arbitrary host path or represent camera acquisition.

The adapter clears the previous ACK before writing a result. An optional sequence
register carries a stable 16-bit job-derived value and the ACK must match it.
The receiver must implement deduplication: a 16-bit value can collide, and this
protocol does not promise exactly-once effects or transactional delivery across
multiple configured adapters.

## HTTP MES

Configure output field mappings, timeout and required acceptance/job ACK fields.
Nested input and ACK fields use dotted paths. An HTTP 2xx response alone is not
acceptance. The acceptance value must match the configured JSON type and value,
and the receipt must identify the same job. Legacy configurations with absent
acceptance or job ACK fields fail validation and need explicit valid mappings.
Every attempt sends the exact job ID in `Idempotency-Key`; the receiver must
retain and use that identity to prevent repeated processing.

Model verdict/result and delivery outcome are stored separately. Receiver
rejection, timeout or disconnect leaves `delivery_error` and operational REVIEW;
the saved model result is retained. The operator can explicitly retry ACK
delivery for that same job. The runtime uses a durable outbox and records attempts.

Authentication values are masked on readback and preserved when left empty at
the same endpoint. Changing endpoints requires an explicit replacement or clear.
The current private local adapter file is not an OS credential store; secret-store
and target security acceptance remain separate requirements.

## Simulator and device evidence

The panel's success/reject/timeout tests create short-lived owned loopback
receivers and run real socket adapters. They do not contact the saved destination
or verify its mapping. Browser/macOS Electron tests also run a deterministic
untrained CPU inspection, send its result to an owned HTTP receiver, observe
rejection and explicitly retry with the same identity. These verify software
contracts, persistence and UI handling. Physical PLC/MES, production security,
representative model quality and Windows acceptance are separate.

`ResultDeliveryAdapter` defines the optional OPC UA receipt boundary. No OPC UA
SDK/provider is bundled, and no OPC UA target execution is claimed.
