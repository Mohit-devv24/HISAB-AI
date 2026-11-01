import React from 'react';

export const Button: React.FC<React.ButtonHTMLAttributes<HTMLButtonElement>> = ({ children, className = '', ...props }) => (
  <button
    className={`px-4 py-2 rounded-xl font-semibold transition active:scale-95 bg-blue-600 hover:bg-blue-500 text-white ${className}`}
    {...props}
  >
    {children}
  </button>
);
