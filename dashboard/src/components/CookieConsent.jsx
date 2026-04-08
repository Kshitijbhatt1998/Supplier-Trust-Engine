import React, { useState, useEffect } from 'react';

const CookieConsent = () => {
  const [isVisible, setIsVisible] = useState(false);

  useEffect(() => {
    const consent = localStorage.getItem('sourceguard_cookie_consent');
    if (!consent) {
      setIsVisible(true);
    }
  }, []);

  const handleAccept = () => {
    localStorage.setItem('sourceguard_cookie_consent', 'true');
    setIsVisible(false);
    // Initialize your analytics here if needed
  };

  if (!isVisible) return null;

  return (
    <div className="fixed bottom-0 w-full bg-gray-900 text-white p-4 flex justify-between items-center z-50">
      <div className="flex-1">
        <p className="text-sm">
          SourceGuard uses essential cookies to manage your API session and track usage quotas. 
          By continuing to use the dashboard, you agree to our{' '}
          <a href="/TERMS_OF_SERVICE.md" target="_blank" rel="noopener noreferrer" className="underline hover:text-blue-400">Terms of Service</a>{' '}
          and{' '}
          <a href="/PRIVACY_POLICY.md" target="_blank" rel="noopener noreferrer" className="underline hover:text-blue-400">Privacy Policy</a>.
        </p>
      </div>
      <button 
        onClick={handleAccept}
        className="bg-blue-600 hover:bg-blue-700 text-white font-bold py-2 px-4 rounded ml-4 transition-colors duration-200"
      >
        Acknowledge
      </button>
    </div>
  );
};

export default CookieConsent;
