"""Publish only the staged public site files, keeping a private rollback copy."""
import json

from production_remote import connect
from production_release import RELEASE, TAG, remote_python, save


def deploy():
    with connect('node1') as client:
        result = json.loads(remote_python(client, f'''
            import hashlib, json, os, pathlib, shutil, subprocess
            source=pathlib.Path({RELEASE!r})/'site'
            target=pathlib.Path('/opt/buggcraft-site')
            backup=pathlib.Path('/var/backups/buggcraft-native-'+{TAG!r})/'site'
            assert source.is_dir() and target.is_dir()
            assert not backup.exists(), 'Site backup already exists; inspect instead of overwriting'
            subprocess.run(['nginx','-t'],check=True,capture_output=True)
            backup.mkdir(mode=0o700)
            files=[]
            for path in sorted(source.rglob('*')):
                if not path.is_file(): continue
                assert not path.is_symlink()
                relative=path.relative_to(source)
                destination=target/relative
                assert destination.resolve().is_relative_to(target.resolve()), 'Unsafe site target'
                before=destination.read_bytes() if destination.exists() else None
                old=backup/relative
                if before is not None:
                    old.parent.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(destination,old)
                files.append((relative,before))
            installed=[]
            try:
                for relative,before in files:
                    destination=target/relative
                    destination.parent.mkdir(parents=True,exist_ok=True)
                    temporary=destination.with_name(destination.name+'.native-'+{TAG!r})
                    assert not temporary.exists()
                    shutil.copyfile(source/relative,temporary)
                    temporary.chmod(0o644)
                    os.replace(temporary,destination)
                    installed.append((relative,before))
                subprocess.run(['nginx','-t'],check=True,capture_output=True)
                rows=[]
                for relative,before in files:
                    contents=(target/relative).read_bytes()
                    assert contents==(source/relative).read_bytes()
                    rows.append(dict(path=relative.as_posix(),
                        before_sha256=hashlib.sha256(before).hexdigest() if before is not None else None,
                        after_sha256=hashlib.sha256(contents).hexdigest()))
                result=dict(files=rows,backup=str(backup),nginx_configuration_valid=True)
                (backup/'deployment-result.json').write_text(json.dumps(result,indent=2))
                print(json.dumps(result))
            except BaseException:
                for relative,before in reversed(installed):
                    destination=target/relative
                    if before is None:
                        destination.unlink()
                    else:
                        temporary=destination.with_name(destination.name+'.native-rollback')
                        shutil.copy2(backup/relative,temporary)
                        os.replace(temporary,destination)
                raise
        '''))
    save('site-deployed.json', result)
    print(json.dumps(dict(files=len(result['files']), backup=result['backup'],
                         nginx_configuration_valid=True)))


if __name__ == '__main__':
    deploy()
