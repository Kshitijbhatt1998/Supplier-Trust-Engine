// Reusable helpers

export function scoreColor(score) {
  if (score >= 80) return 'high'
  if (score >= 60) return 'mid'
  if (score >= 40) return 'low'
  return 'danger'
}

export function scoreLabel(score) {
  if (score >= 80) return 'Low Risk'
  if (score >= 60) return 'Moderate'
  if (score >= 40) return 'Elevated'
  return 'High Risk'
}

export function flagShort(flag) {
  // Shorten long SHAP flag strings for compact display
  const map = {
    'Short operating history': 'Short history',
    'Inactive recently (no recent shipments)': 'Inactive recently',
    'High customer concentration (captive factory risk)': 'High concentration',
    'Too few HS codes (limited product range)': 'Few HS codes',
    'Extremely broad product spread (middleman signal)': 'Broad HS spread',
    'Low shipment frequency': 'Low frequency',
    'Missing or weak certifications': 'Weak certs',
    'No valid certifications found': 'No valid certs',
    'Has expired certifications (lapsed compliance)': 'Expired certs',
    'Low shipment volume vs. industry peers': 'Low volume',
    'Higher-risk manufacturing country': 'Country risk',
    'Low total shipment count': 'Low shipments',
    'Low average monthly shipments': 'Low monthly vol',
    'Very few distinct buyers': 'Few buyers',
    'No valid certifications': 'No certs',
  }
  return map[flag] || flag
}

export const COUNTRIES = [
  'India', 'China', 'Bangladesh', 'Turkey', 'Vietnam',
  'Portugal', 'Italy', 'Pakistan', 'Germany',
]

export const CERT_TYPES = ['gots', 'oekotex', 'grs']
