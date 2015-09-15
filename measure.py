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


def extract_commit(name, repo, ref, target_dir):
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


def sloccount_physical_source_lines_of_code(name, source_dir):
    # There's no 'machine-readable' output feature of 'sloccount' that we could
    # use, we just match against a string in the output.

    ybd.app.log(name, "Counting lines of code with sloccount in", source_dir)
    text = subprocess.check_output(['sloccount', source_dir])

    for line in text.decode('ascii').splitlines():
        if line.startswith('Total Physical Source Lines of Code (SLOC)'):
            number_string = line.split()[-1]
            number_string = number_string.replace(',', '')
            return int(number_string)

    raise RuntimeError("Unexpected output from sloccount: %s" % text)


def git_active_days(gitdir, ref):
    # This uses the 'git-active-days' script in the same repo.

    script = os.path.join(os.path.dirname(__file__), 'git-active-days')
    text = subprocess.check_output([script, '--ref', ref, gitdir])

    return int(text.decode('ascii').strip())


def measure_component_source_repo(name, repo, ref):
    # Don't do this in /tmp, because on an OpenStack VM's root disk it will
    # take FOREVER. FIXME: should get the path from YBD's config probably.
    source_dir = tempfile.mkdtemp(dir='/src/tmp')
    try:
        extract_commit(name, repo, ref, source_dir)

        sloc = sloccount_physical_source_lines_of_code(name, source_dir)

        gitdir = os.path.join(ybd.app.config['gits'],
                              ybd.repos.get_repo_name(repo))
        active_days = git_active_days(gitdir, ref)

        return {
            'name': name,
            'sloc': sloc,
            'git_active_days': active_days
        }
    finally:
        if os.path.exists(source_dir):
            shutil.rmtree(source_dir)


def write_csv_file(f, rows, columns):
    # This is really naive, probably we can use a library.

    f.write(', '.join(columns) + '\n')

    for item in rows:
        line = ', '.join(str(item[column]) for column in columns)
        f.write(line + '\n')


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

    results = {}
    for c in components:
        if 'repo' in c and 'ref' in c:
            key = (c['repo'], c['ref'])
            if key not in results:
                stats = measure_component_source_repo(
                    c['name'], c['repo'], c['ref'])
                results[key] = stats

    with open('results.csv', 'w') as f:
        write_csv_file(
            f,
            rows=results.values(),
            columns=['name', 'sloc', 'git_active_days'])

try:
    main()
except RuntimeError as e:
    sys.stderr.write('ERROR: %s\n' % e)
    sys.exit(1)
