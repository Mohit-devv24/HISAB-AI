import React from 'react';

export const NotificationBell: React.FC<{ count: number }> = ({ count }) => (
  <div className="relative cursor-pointer">
    <span className="text-xl">🔔</span>
    {count > 0 && (
      <span className="absolute -top-1 -right-1 bg-rose-500 text-white text-[10px] font-bold px-1.5 py-0.5 rounded-full">
        {count}
      </span>
    )}
  </div>
);
