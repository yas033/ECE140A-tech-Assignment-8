CREATE TABLE IF NOT EXISTS devices (
  mac_address VARCHAR(17) PRIMARY KEY,
  first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS readings (
  id INT AUTO_INCREMENT PRIMARY KEY,
  mac_address VARCHAR(17) NOT NULL,
  thermistor_temp FLOAT NOT NULL,
  prediction VARCHAR(16) NOT NULL,
  confidence FLOAT NOT NULL,
  pixels JSON NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (mac_address) REFERENCES devices(mac_address)
);

-- (optional) store last command (not required by rubric, but helpful)
CREATE TABLE IF NOT EXISTS commands (
  id INT AUTO_INCREMENT PRIMARY KEY,
  command VARCHAR(32) NOT NULL,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);