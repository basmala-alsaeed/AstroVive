const ECG="M0 14h12l4-10 5 20 4-14 3 4h12l4-8 5 16 3-8h10";
function spark(v,w=120,h=28){if(!v||v.length<2)return'<path d="'+ECG+'"/>';const a=Math.min(...v),b=Math.max(...v)||1,r=(b-a)||1;
return'<path d="'+v.map((x,i)=>(i?'L':'M')+(i*w/(v.length-1)).toFixed(1)+' '+(h-2-(x-a)/r*(h-4)).toFixed(1)).join('')+'"/>'}
