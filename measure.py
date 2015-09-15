import ybd

import os
import shutil
import subprocess
import sys
import tempfile


def walk_dependencies(definitions, component):
    '''Yields 'component' and all of its dependencies, in depth-first order.'''

    done = set()

    def walk(component):
        dependencies = component.get('build-depends', [])
        dependencies.extend(component.get('contents', []))
        for system in component.get('systems', []):
            dependencies.append(system['path'])

        for dep_path in dependencies:
            if dep_path not in done:
                done.add(dep_path)

                dep = definitions.get(dep_path)
                for child_dep in walk(dep):
                    yield child_dep
                yield dep

    return walk(component)


def load_ybd_definitions(target_filename):
    '''Initialise YBD and load all definitions from current working directory.

    Returns a ybd Definitions instance.

    '''
    # FIXME: huge hack, need to bundle YBD's default config files
    # in the 'ybd' module, instead.
    fake_file = '/src/ybd/ybd/__main__.py'

    ybd.app.setup(args=[fake_file, target_filename])

    definitions = ybd.definitions.Definitions()
    return definitions


def extract_commit(repo, ref, target_dir):
    '''Check out a single commit (or tree) from a Git repo.

    The ybd.repos.checkout() function actually clones the entire repo, so this
    function is much quicker when you don't need to copy the whole repo into
    target_dir.

    '''
    gitdir = os.path.join(ybd.app.config['gits'],
                          ybd.repos.get_repo_name(repo))

    if not os.path.exists(gitdir):
        ybd.repos.mirror(name, repo)
    elif not ybd.repos.mirror_has_ref(gitdir, ref):
        ybd.repos.update_mirror(name, repo, gitdir)

    with tempfile.NamedTemporaryFile() as git_index_file:
        git_env = os.environ.copy()
        git_env['GIT_INDEX_FILE'] = git_index_file.name
        git_env['GIT_WORK_TREE'] = target_dir

        ybd.app.log(repo, 'Extracting commit', ref)
        subprocess.check_call(
            ['git', 'read-tree', ref], env=git_env, cwd=gitdir)
        subprocess.check_call(
            ['git', 'checkout-index', '--all'], env=git_env, cwd=gitdir)


def measure_component_source_repo(name, repo, ref):
    # Don't do this in /tmp, because on an OpenStack VM's root disk it will
    # take FOREVER. FIXME: should get the path from YBD's config probably.
    source_dir = tempfile.mkdtemp(dir='/src/tmp')
    try:
        extract_commit(repo, ref, source_dir)

        os.listdir(source_dir)
    finally:
        if os.path.exists(source_dir):
            shutil.rmtree(source_dir)


def main():
    if len(sys.argv) != 2:
        raise RuntimeError(
            "Please run this script in a Baserock definitions tree, and pass "
            "the path to a definition file as an argument.")

    target_filename = sys.argv[1]

    definitions = load_ybd_definitions(target_filename)

    target = definitions.get(target_filename)

    if target is None:
        raise RuntimeError("Did not find component '%s'" % target_filename)

    # Now we need to get the build graph for 'target', and get all
    # the repos that are involved.
    components = list(walk_dependencies(definitions, target))

    done = set()
    for c in components:
        if 'repo' in c and 'ref' in c:
            key = (c['repo'], c['ref'])
            if key not in done:
                done.add(key)
                measure_component_source_repo(c['name'], c['repo'], c['ref'])


try:
    main()
except RuntimeError as e:
    sys.stderr.write('ERROR: %s\n' % e)
    sys.exit(1)
