import React, { useState } from 'react';
import { AuthService } from '../../services/auth.service';

export const LoginForm: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    await AuthService.login({ email, password });
    window.location.reload();
  };

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3 max-w-sm mx-auto p-6 bg-slate-900 rounded-2xl border border-slate-800">
      <h2 className="text-xl font-bold text-white">Login to Hisab AI</h2>
      <input type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="Shopkeeper Email" className="p-3 rounded-lg bg-slate-800 border border-slate-700 text-white" required />
      <input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Password" className="p-3 rounded-lg bg-slate-800 border border-slate-700 text-white" required />
      <button type="submit" className="p-3 bg-blue-600 text-white font-bold rounded-lg hover:bg-blue-500">Login</button>
    </form>
  );
};
