// How the one who did something is named in a timeline or an activity list: a person or an API key by
// their label, the system by the part of it that acted ("Monitoring"), and anyone unknown as someone.

export function actorName(actor) {
  const label = actor?.label;
  if (actor?.type === 'system') {
    return label ? `${label.charAt(0).toUpperCase()}${label.slice(1)}` : 'The system';
  }
  return label || 'Someone';
}
