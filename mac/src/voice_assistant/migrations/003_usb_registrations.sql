-- USB registration history shares the device-credential transaction.
-- No plaintext token, USB serial number, or Wi-Fi password is stored here.
CREATE TABLE usb_registrations (
  usb_identity TEXT NOT NULL CHECK (
    length(usb_identity) = 64 AND usb_identity NOT GLOB '*[^0-9a-f]*'
  ),
  server_id TEXT NOT NULL,
  device_id TEXT NOT NULL REFERENCES devices(id),
  provisioned_at REAL NOT NULL CHECK (provisioned_at >= 0),
  PRIMARY KEY (usb_identity, server_id)
);
CREATE INDEX idx_usb_registrations_device ON usb_registrations(device_id);
