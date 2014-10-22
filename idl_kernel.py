from IPython.kernel.zmq.kernelbase import Kernel
from IPython.utils.path import locate_profile
from IPython.core.displaypub import publish_display_data
from pexpect import replwrap,EOF

import signal
from subprocess import check_output
import tempfile
import re
from glob import glob
from shutil import rmtree
from base64 import b64encode

__version__ = '0.2'

version_pat = re.compile(r'Version (\d+(\.\d+)+)')

class IDLKernel(Kernel):
    implementation = 'IDL_kernel'
    implementation_version = __version__
    language = 'IDL'
    @property
    def language_version(self):
        m = version_pat.search(self.banner)
        return m.group(1)

    _banner = None
    @property
    def banner(self):
        if self._banner is None:
            try:
                self._banner = check_output(['idl', '-e "" ']).decode('utf-8')
            except:
                self._banner = check_output(['gdl', '--version']).decode('utf-8')
        return self._banner
    
    def __init__(self, **kwargs):
        Kernel.__init__(self, **kwargs)
        self._start_idl()

        try:
            self.hist_file = os.path.join(locate_profile(),'idl_kernel.hist')
        except:
            self.hist_file = None
            self.log.warn('No default profile found, history unavailable')

        self.max_hist_cache = 1000
        self.hist_cache = []

    def _start_idl(self):
        # Signal handlers are inherited by forked processes, and we can't easily
        # reset it from the subprocess. Since kernelapp ignores SIGINT except in
        # message handlers, we need to temporarily reset the SIGINT handler here
        # so that IDL and its children are interruptible.
        sig = signal.signal(signal.SIGINT, signal.SIG_DFL)
        try:
            self.idlwrapper = replwrap.REPLWrapper("idl",u"IDL> ",None)
        except:
            self.idlwrapper = replwrap.REPLWrapper("gdl",u"GDL> ",None)
        finally:
            signal.signal(signal.SIGINT, sig)

        self.idlwrapper.run_command("!quiet=1 & defsysv,'!inline',0".rstrip(), timeout=None)

    def do_execute(self, code, silent, store_history=True, user_expressions=None,
                   allow_stdin=False):

        if not code.strip():
            return {'status': 'ok', 'execution_count': self.execution_count,
                    'payloads': [], 'user_expressions': {}}
        elif (code.strip() == 'exit' or code.strip() == 'quit'):
            self.do_shutdown(False)
            return {'status':'abort','execution_count':self.execution_count}
 
        if code.strip() and store_history:
            self.hist_cache.append(code.strip())

        interrupted = False
        tfile = tempfile.NamedTemporaryFile(mode='w+t')
        plot_dir = tempfile.mkdtemp()
        plot_format = 'png'

        postcall = """
		device,window_state=winds_arefgij
		if !inline and total(winds_arefgij) ne 0 then begin
            w_CcjqL6MA = where(winds_arefgij ne 0,nw_CcjqL6MA)
            for i_KEv8eW6E=0,nw_CcjqL6MA-1 do begin
                wset,w_CcjqL6MA[i_KEv8eW6E]
                ; load color table info
                tvlct, r_m9QVFuGP,g_jeeyfQkN,b_mufcResT, /get
                img_bGr4ea3s = tvrd()
                wdelete

                outfile_c5BXq4dV = '%(plot_dir)s/__fig'+strtrim(i_KEv8eW6E,2)+'.png'
                ; Set the colors for each channel
                s_m77YL7Gd = size(img_bGr4ea3s)
                ii_rsApk4JS=bytarr(3,s_m77YL7Gd[1],s_m77YL7Gd[2])
                ii_rsApk4JS[0,*,*]=r_m9QVFuGP[img_bGr4ea3s]
                ii_rsApk4JS[1,*,*]=g_jeeyfQkN[img_bGr4ea3s]
                ii_rsApk4JS[2,*,*]=b_mufcResT[img_bGr4ea3s]

                ; Write the PNG if the image is not blank
                if total(img_bGr4ea3s) ne 0 then begin
                    write_png, outfile_c5BXq4dV, ii_rsApk4JS, r_m9QVFuGP, g_jeeyfQkN, b_mufcResT
                endif
            endfor
		endif
        end
        """ % locals()

        try:
            tfile.file.write(code.rstrip()+postcall.rstrip())
            tfile.file.close()
            output = self.idlwrapper.run_command(".run "+tfile.name, timeout=None)

            # Publish images if there are any
            images = [open(imgfile, 'rb').read() for imgfile in glob("%s/*.png" % plot_dir)]

            display_data=[]

            for image in images:
                display_data.append({'image/png': b64encode(image).decode('ascii')})

            for data in display_data:
                self.send_response(self.iopub_socket, 'display_data',{'data':data})
        except KeyboardInterrupt:
            self.idlwrapper.child.sendintr()
            interrupted = True
            self.idlwrapper._expect_prompt()
            output = self.idlwrapper.child.before
        except EOF:
            output = self.idlwrapper.child.before + 'Restarting IDL'
            self._start_idl()
        finally:
            tfile.close()
            rmtree(plot_dir)

        if not silent:
            stream_content = {'name': 'stdout', 'text':output}
            self.send_response(self.iopub_socket, 'stream', stream_content)
        
        if interrupted:
            return {'status': 'abort', 'execution_count': self.execution_count}
        
        try:
            exitcode = int(self.run_command('print,0').rstrip())
        except Exception:
            exitcode = 1

        if exitcode:
            return {'status': 'error', 'execution_count': self.execution_count,
                    'ename': '', 'evalue': str(exitcode), 'traceback': []}
        else:
            return {'status': 'ok', 'execution_count': self.execution_count,
                    'payloads': [], 'user_expressions': {}}

    def do_history(self, hist_access_type, output, raw, session=None,
                   start=None, stop=None, n=None, pattern=None, unique=False):

        if not self.hist_file:
            return {'history': []}

        if not os.path.exists(self.hist_file):
            with open(self.hist_file, 'wb') as f:
                f.write('')

        with open(self.hist_file, 'rb') as f:
            history = f.readlines()

        history = history[:self.max_hist_cache]
        self.hist_cache = history
        self.log.debug('**HISTORY:')
        self.log.debug(history)
        history = [(None, None, h) for h in history]

        return {'history': history}

    def do_shutdown(self, restart):
        self.log.debug("**Shutting down")

        self.idlwrapper.child.kill(signal.SIGKILL)

        if self.hist_file:
            with open(self.hist_file,'wb') as f:
                data = '\n'.join(self.hist_cache[-self.max_hist_cache:])
                fid.write(data.encode('utf-8'))

        return {'status':'ok', 'restart':restart}

if __name__ == '__main__':
    from IPython.kernel.zmq.kernelapp import IPKernelApp
    IPKernelApp.launch_instance(kernel_class=IDLKernel)
