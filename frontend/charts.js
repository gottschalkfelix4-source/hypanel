// Measured samples only. Missing readings stay gaps; history survives reloads.
export class Chart {
  constructor(canvas, {key, color, max, unit}) {
    Object.assign(this,{canvas,key,color,max,unit,rows:[]});
    this.observer=new ResizeObserver(()=>this.draw());this.observer.observe(canvas);
    canvas.addEventListener('pointermove',e=>{
      const rect=canvas.getBoundingClientRect();const i=Math.round((e.clientX-rect.left)/rect.width*(this.rows.length-1));
      const p=this.rows[i];canvas.title=p?`${new Date(p.at*1000).toLocaleTimeString('de-DE')} · ${p[this.key]===null?'Nicht verfügbar':Number(p[this.key]).toFixed(1)+' '+this.unit}`:'';
    });
  }
  set(rows){this.rows=rows;this.draw();}
  draw(){
    const {canvas,rows,key,color}=this;const w=canvas.clientWidth,h=canvas.clientHeight,dpr=devicePixelRatio||1;
    if(!w||!h)return;canvas.width=w*dpr;canvas.height=h*dpr;const c=canvas.getContext('2d');c.scale(dpr,dpr);
    const left=35,bottom=22,top=8,plot=h-bottom-top;
    const values=rows.map(p=>p[key]).filter(v=>v!==null&&Number.isFinite(v));
    const max=this.max||Math.max(1,...values)*1.1;
    c.font='10px Consolas';c.fillStyle='#a1a1ab';c.strokeStyle='#2c2c33';
    for(let i=0;i<=3;i++){const y=top+plot*i/3;c.beginPath();c.moveTo(left,y);c.lineTo(w,y);c.stroke();c.fillText((max*(1-i/3)).toFixed(0),1,y+4);}
    if(!values.length){c.fillText('Noch keine Messwerte',left+12,top+plot/2);return;}
    const span=rows.length>1?rows.at(-1).at-rows[0].at:1;const width=Math.max(2,(w-left)/Math.max(rows.length,1)-2);
    c.fillStyle=color;
    rows.forEach(p=>{const v=p[key];if(v===null||!Number.isFinite(v))return;const x=left+(p.at-rows[0].at)/span*(w-left-width);const height=Math.min(plot,Math.max(1,v/max*plot));c.fillRect(x,top+plot-height,width,height);});
    c.fillStyle='#a1a1ab';c.fillText(new Date(rows[0].at*1000).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}),left,h-4);
    c.fillText(new Date(rows.at(-1).at*1000).toLocaleTimeString('de-DE',{hour:'2-digit',minute:'2-digit'}),Math.max(left,w-38),h-4);
  }
  destroy(){this.observer.disconnect();}
}
