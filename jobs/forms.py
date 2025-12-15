from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.forms import inlineformset_factory

from .models import JobDescription, QualificationCriterion


class RememberMeAuthenticationForm(AuthenticationForm):
    remember_me = forms.BooleanField(required=False, initial=True, label="Remember me")


class JobDescriptionForm(forms.ModelForm):
    class Meta:
        model = JobDescription
        fields = ["name", "summary", "active"]
        widgets = {
            "summary": forms.Textarea(attrs={"rows": 6, "class": "rich-text", "style": "overflow:auto;"}),
        }


class QualificationCriterionForm(forms.ModelForm):
    class Meta:
        model = QualificationCriterion
        fields = ["detail"]
        widgets = {
            "detail": forms.Textarea(attrs={"rows": 6, "class": "rich-text", "style": "overflow:auto;"}),
        }


QualificationFormSet = inlineformset_factory(
    JobDescription,
    QualificationCriterion,
    form=QualificationCriterionForm,
    extra=1,
    can_delete=True,
)


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class ResumeUploadForm(forms.Form):
    job = forms.ModelChoiceField(queryset=JobDescription.objects.none(), help_text="Pick an active JD to score the uploaded resumes against.")
    use_folder = forms.BooleanField(
        required=False,
        initial=False,
        label="Upload a folder",
        help_text="Toggle to pick an entire folder (where supported) instead of individual files.",
    )
    files = forms.FileField(
        required=False,
        widget=MultipleFileInput(attrs={"multiple": True}),
        help_text="Upload one or many PDF/TXT/DOCX files or toggle folder upload.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["job"].queryset = JobDescription.objects.filter(active=True)
