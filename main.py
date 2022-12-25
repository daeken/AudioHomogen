import os, os.path, subprocess, sys, tempfile
from pyparsedvd import load_vts_pgci
import cueparser
import ffmpeg
from glob import glob
from pprint import pprint

def read_ifo(fn):
	with open(fn, 'rb') as fp:
		return load_vts_pgci(fp)

def read_cue(fn):
	with open(fn, 'rb') as fp:
		cuesheet = cueparser.CueSheet()
		cuesheet.setOutputFormat('%performer% - %title%\n%file%\n%tracks%', '%performer% - %title%')
		cuesheet.setData(fp.read().decode('utf8'))
		cuesheet.parse()
		return cuesheet

codec_priority = ['ac3', 'dts', 'pcm_dvd']

def process_dvd(ipath, output):
	if len(glob('%s/*.VOB' % ipath)) == 0:
		if os.path.exists(ipath + '/VIDEO_TS'):
			return process_dvd(ipath + '/VIDEO_TS', output)
		print('Presumed DVD but could not find VOB files.')
		return 1
	
	titles = {}
	for fn in glob('%s/VTS_*.VOB' % ipath):
		sfn = fn.rsplit('/', 1)[-1]
		_, title, _ = sfn.split('_')
		if title not in titles:
			titles[title] = 0
		titles[title] += os.path.getsize(fn)
	biggest = max(titles, key=titles.get)
	ifo = read_ifo('%s/VTS_%s_0.IFO' % (ipath, biggest))
	pc = ifo.program_chains[0]
	pprint(pc)
	num_tracks = len(pc.playback_times)
	vobs = sorted(glob('%s/VTS_%s_*.VOB' % (ipath, biggest)))
	assert len(vobs) > 0
	fsi = ffmpeg.probe(vobs[0])['streams']
	si = [x for x in fsi if 'channels' in x and x['channels'] > 2]
	if len(si) == 0:
		print('Could not find multichannel audio stream!')
		return 1
	si = sorted(si, key=lambda x: codec_priority.index(x['codec_name']) if x['codec_name'] in codec_priority else 10000)
	before = 0
	for elem in fsi:
		if elem is si[0]:
			break
		before += 1

	while True:
		artist_name = input('Artist name: ')
		album_name = input('Album name: ')
		if input('"%s" by %s. Is this correct? y/n: ' % (album_name, artist_name)) == 'y':
			break

	while True:
		print('Please enter the track names. If you wish to discard a track, simply hit enter.')
		track_names = []
		for i, pt in enumerate(pc.playback_times):
			name = input('Track %i (%s) name: ' % (i + 1, '%02i:%02i:%02i' % (pt.hours, pt.minutes, pt.seconds) if pt.hours != 0 else '%02i:%02i.%03i' % (pt.minutes, pt.seconds, int(pt.frames / 30 * 1000))))
			track_names.append(None if name == '' else name)
		print('Confirm:')
		for i, name in enumerate(track_names):
			if name is None:
				print('Track %i is DISCARDED' % (i + 1))
			else:
				print('Track %i is "%s"' % (i + 1, name))
		if input('Is this correct? y/n: ') == 'y':
			break
	
	fd, combined = tempfile.mkstemp(suffix='.vob')
	os.close(fd)
	with open(combined, 'wb') as ofp:
		for elem in vobs:
			with open(elem, 'rb') as ifp:
				ofp.write(ifp.read())
	
	start = 0
	procs = []
	for i, elem in enumerate(pc.playback_times):
		end = start + (elem.hours * 60 * 60 + elem.minutes * 60 + elem.seconds + elem.frames / 30)
		name = track_names[i]
		if name is None:
			start = end
			continue
		format_time = lambda x: '%02i:%02i:%02.3f' % (int(x / 60 / 60), int(x / 60 % 60), x % 60)
		args = ['ffmpeg', '-i', combined, '-ss', format_time(start), '-to', format_time(end), '-map', '0:4', '-map', '-0:v', '-metadata', 'title=%s' % name, '-metadata', 'artist=%s' % artist_name, '-metadata', 'album=%s' % album_name, '%s/%i - %s.flac' % (output, i + 1, name), '-y']
		print(args)
		procs.append(subprocess.Popen(args, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL))
		start = end
	
	for i, elem in enumerate(procs):
		elem.wait()
		print('Process', i, 'completed')
	os.remove(combined)

def process_cue(ipath, output):
	cue = read_cue(ipath)
	ipath = os.path.dirname(os.path.abspath(ipath)) + '/' + cue.file
	artist_name = cue.performer
	album_name = cue.title

	format_time = lambda x: '%02i:%02i:%02i.%03i' % (x.days * 24 + int(x.seconds / 60 / 60), int(x.seconds / 60 % 60), int(x.seconds % 60), int(x.microseconds / 1000))

	procs = []
	for track in cue.tracks:
		offset = cueparser.offsetToTimedelta(track.offset)
		args = ['ffmpeg', '-i', ipath, '-ss', format_time(offset)]
		if track.duration is not None:
			args += ['-to', format_time(offset + track.duration)]
		args += ['-metadata', 'title=%s' % track.title, '-metadata', 'artist=%s' % artist_name, '-metadata', 'album=%s' % album_name, '%s/%i - %s.flac' % (output, track.number, track.title), '-y']
		print(args)
		procs.append(subprocess.Popen(args, stdout=subprocess.DEVNULL, stdin=subprocess.DEVNULL))
	for i, elem in enumerate(procs):
		elem.wait()
		print('Process', i, 'completed')

def main(ipath, output):
	if not os.path.exists(ipath):
		print('Invalid path', ipath)
		return 1
	
	os.makedirs(output, exist_ok=True)
	
	if os.path.isdir(ipath):
		return process_dvd(ipath, output)

	if ipath.endswith('.cue'):
		return process_cue(ipath, output)
	
	firstmeg = open(ipath, 'rb').read(1024 * 1024)
	if b'SACDMTOC' in firstmeg:
		return process_sacd(ipath, output)
	if b'matroska' in firstmeg:
		return process_mkv(ipath, output)
	print('Unknown file format')
	return 1

if __name__ == '__main__':
	sys.exit(main(*sys.argv[1:]))
