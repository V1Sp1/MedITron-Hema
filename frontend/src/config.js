// The Python server serves the site on the same origin; port 5173 is the optional UI dev server.
// Static previews remain local. An API failure never falls back to a demo or local result.
const localBrowser=typeof location!=='undefined'&&['localhost','127.0.0.1'].includes(location.hostname);
export const config = {mode:localBrowser?'api':'local',baseUrl:localBrowser&&location.port==='5173'?`http://${location.hostname}:8000`:'',paths:{patient:'/api/patient/predict',doctor:'/api/doctor/predict',recommendations:'/api/recommendations',pdf:'/api/observations/pdf'}};
