import React from 'react';
import { PaymentClaim } from '../../types';

export const PaymentClaims: React.FC<{ claims: PaymentClaim[] }> = ({ claims }) => (
  <div className="p-4 bg-slate-900 rounded-2xl border border-slate-800">
    <h3 className="text-md font-bold text-white mb-3">UPI Payment Verification</h3>
    {claims.length === 0 ? <p className="text-xs text-slate-500">No pending payment claims</p> : null}
  </div>
);
