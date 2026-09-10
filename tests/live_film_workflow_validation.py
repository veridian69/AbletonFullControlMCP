"""Configured MCP integration: real picture/saved tempo, synthetic calibration only."""
import argparse
import asyncio
from contextlib import AsyncExitStack
import json
import os
from pathlib import Path
import tempfile
import tomllib

import numpy as np
import soundfile as sf
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def run(args):
    config=tomllib.loads(args.config.read_text(encoding='utf-8'))
    async with asyncio.timeout(120), AsyncExitStack() as stack:
        clients=[]
        for name in ('davinci-resolve-film','ableton-film'):
            e=config['mcp_servers'][name]
            streams=await stack.enter_async_context(stdio_client(StdioServerParameters(
                command=e['command'],args=e['args'],cwd=e['cwd'],env=dict(os.environ,**e.get('env',{})))))
            client=await stack.enter_async_context(ClientSession(*streams))
            await client.initialize(); clients.append(client)
        resolve,ableton=clients
        async def call(client,name,arguments):
            result=await client.call_tool(name,arguments)
            assert not result.isError,result
            data=result.structuredContent or json.loads(next(c.text for c in result.content if c.type=='text'))
            data=data.get('result',data)
            return {k:v for k,v in data.items() if k not in ('_operation','_versioning')}
        sheet=await call(resolve,'timeline_markers',{'action':'export_cue_sheet'})
        picture=await call(resolve,'timeline',{'action':'film_snapshot'})
        assert picture['schema']=='film-snapshot/v1' and picture['cue_sheet']==sheet,picture
        comparison=await call(resolve,'timeline_markers',{'action':'compare_cue_sheets','params':{
            'before':sheet,'after':sheet,'before_snapshot':picture['snapshot'],'after_snapshot':picture['snapshot']}})
        assert comparison['status']=='compared' and comparison['changes']==[],comparison
        assert 'editorial_comparison' in comparison['picture_impact'],comparison
        baseline=await call(resolve,'timeline',{'action':'film_preflight'})
        tempo=await call(ableton,'film_tempo_read',{'source_set_path':str(args.source_set)})
        assert tempo['status']=='ok' and tempo['saved_only'] is True,tempo
        with tempfile.TemporaryDirectory(prefix='film-calibration-mcp-') as folder:
            root=Path(folder)
            probe=await call(ableton,'generate_calibration_probe',{'output_path':str(root/'probe.wav'),'seed':73})
            audio,rate=sf.read(probe['path'],always_2d=True)
            capture_paths=[]
            for index,gain in enumerate((.6,.4)):
                captured=np.vstack((np.zeros((1373,2)),audio*gain,np.zeros((2000,2))))
                path=root/f'capture-{index}.wav'; sf.write(path,captured,rate,subtype='PCM_24')
                capture_paths.append(str(path))
            configuration={'audio_path':'synthetic fixture only','device':'no hardware',
                'driver':'no driver','sample_rate':rate,'buffer_size_samples':256,
                'settings':{'fixture':True,'gain_only':True}}
            profile=await call(ableton,'measure_capture_calibration',{'probe':probe,
                'capture_paths':capture_paths,'configuration':configuration})
            assert profile['status']=='measured' and profile['offset_samples']==1373,profile
            checked=await call(ableton,'validate_capture_calibration',{'profile':profile,'configuration':configuration})
            assert checked['status']=='valid' and checked['evidence_remeasured'] is True,checked
            stale=await call(ableton,'validate_capture_calibration',{'profile':profile,
                'configuration':{**configuration,'buffer_size_samples':512}})
            assert stale['status']=='error',stale
            delivery=await call(ableton,'film_stems_prepare',{
                'stems':[{'name':f'fixture-{i}','path':path,'source_start_sample':-1373}
                         for i,path in enumerate(capture_paths)],
                'output_dir':str(root/'delivery'),'frame_rate':sheet['frame_rate'],
                'start_frame':0,'end_frame':96,'tail_frames':24,
                'sample_rate':rate,'preview':False,'confirm_origins':True})
            assert delivery['status']=='prepared',delivery
            result=await call(resolve,'timeline',{'action':'film_preflight','params':{
                'expected_picture_fingerprint':baseline['picture_fingerprint'],
                'delivery_dir':str(root/'delivery'),
                'tempo_evidence':tempo,'calibration_evidence':profile,'capture_profile':configuration}})
            checks={c['name']:c['status'] for c in result['checks']}
            assert checks['picture_version']=='pass' and checks['tempo']=='warn' and checks['capture_alignment']=='warn',result
            assert checks['delivery']=='pass' and result['status']!='passed',result
        after=await call(resolve,'timeline_markers',{'action':'export_cue_sheet'})
        assert after==sheet,'Resolve markers changed'
        print(json.dumps({'status':'passed','cue_count':len(sheet['cues']),
            'tempo_mode':tempo['mode'],'tempo_bpm':tempo['tempo_bpm'],'calibration_offset_samples':1373,
            'calibration_source':'synthetic fixtures, not hardware','stale_profile_refused':True,
            'preflight_checks':checks,'live_mutations':0}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--source-set',type=Path,required=True)
    asyncio.run(run(parser.parse_args()))
