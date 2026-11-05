'use client';

import { useState } from 'react';
import { TelegramIcon } from '../icons/Icons';

function SettingsField({ label, type = 'text', placeholder, value, onSave }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);

  const handleClick = () => {
    if (editing) {
      onSave(draft);
    }
    setEditing((current) => !current);
  };

  return (
    <div className="settings-field">
      <label>{label}</label>
      <div className="settings-field-row">
        <input
          type={type}
          placeholder={placeholder}
          value={editing ? draft : value}
          onChange={(event) => setDraft(event.target.value)}
          readOnly={!editing}
        />
        <button type="button" className="settings-field-change" onClick={handleClick}>
          {editing ? 'Save' : 'Change'}
        </button>
      </div>
    </div>
  );
}

export default function SettingsForm() {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [phone, setPhone] = useState('');

  return (
    <div className="card settings-form-card">
      <SettingsField label="Email" placeholder="No linked mail" value={email} onSave={setEmail} />
      <SettingsField
        label="Password"
        type="password"
        placeholder="No password set"
        value={password}
        onSave={setPassword}
      />
      <SettingsField label="Phone" placeholder="No linked phone" value={phone} onSave={setPhone} />

      <div className="settings-field">
        <label>Telegram</label>
        <a className="settings-telegram-btn" href="https://t.me/AMLCrypto" target="_blank" rel="noreferrer">
          <TelegramIcon width={18} height={18} />
          Connect Telegram account
        </a>
      </div>
    </div>
  );
}
