'use client';

import { PERMISSIONS } from '../../lib/permissions';
import { useCan } from '../layout/SessionContext';
import { Notice } from '../ui/Notice';

// What the editor (of incidents and of wallet profiles) shows to a person who reaches it by its address
// without being an editor: a plain explanation, not a form the platform would refuse. The editor itself
// is only mounted for an editor, so it asks the platform for nothing otherwise. The platform refuses
// what a person may not do whatever this page shows.
export default function EditorGate({ children }) {
  if (useCan(PERMISSIONS.incidentsCurate)) {
    return children;
  }
  return (
    <div className="app-page">
      <h1>Editor</h1>
      <Notice tone="info" title="Not for your account">
        The incidents and the wallet profiles everybody can read are written by the platform&apos;s editors, and no role makes a
        person one. The operator of this platform does that from the command line.
      </Notice>
    </div>
  );
}
